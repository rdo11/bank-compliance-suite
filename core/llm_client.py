"""
llm_client.py — OpenAI-backed analysis with strict prompt injection,
JSON repair retries and a hard 60-second timeout.

The system prompt forbids hallucination: the model may ONLY use the
provided raw text and markdown tables. Missing numbers become null with
note "Data not found in provided context". Every metric and risk flag
must carry an exact citation (page / table_id / row), satisfying the
EU AI Act explainability requirement.

If the model returns malformed JSON, we retry with a repair prompt
("Fix this JSON: ...") up to ``max_repair_attempts`` times, then fall
back to a schema-valid report with a note on the failure.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from models.schemas import FinalReport

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60
MAX_REPAIR_ATTEMPTS = 3

# Provider registry: all speak the OpenAI chat-completions protocol, so the
# same client works for OpenAI, DeepSeek (~1/30th cost) and Gemini Flash Lite
# (generous free tier) — just set LLM_PROVIDER in .env.
PROVIDERS = {
    "openai": {"base_url": None, "model": "gpt-4o"},
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash-lite",
    },
}

SYSTEM_PROMPT = """You are a financial analyst assistant for the EU AI Act era. \
You analyse Danish annual reports (årsrapport) and produce a structured JSON \
risk summary.

STRICT RULES:
1. Use ONLY the provided raw text and markdown tables. NEVER invent, estimate \
or extrapolate numbers that are not in the context.
2. If a number is not found in the provided context, output "value": null and \
set "note": "Data not found in provided context".
3. Every single metric in "extracted_metrics" and every "risk_flags" entry MUST \
include an exact citation: the page number, the table id (e.g. TABLE_3) and, \
when citing a table, the row number where the figure appears.
4. The "executive_summary" must be exactly three concise sentences about the \
company's financial health.
5. "severity" must be one of: Low, Medium, High, Critical.
6. Units are typically DKK; write "million" or "thousand" explicitly in "unit" \
when the table says so.
7. Numbers must be plain floats (no thousands separators, no "DKK" strings).
8. Your response MUST be a single valid JSON object matching the schema exactly. \
No markdown fences, no commentary outside the JSON.
"""


class LLMClient:
    """Thin OpenAI wrapper: analyse(context) -> FinalReport."""

    def __init__(
        self,
        api_key: str,
        provider: str = "openai",
        model: str = "",
        timeout: int = TIMEOUT_SECONDS,
        max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> None:
        self.provider = provider.lower()
        if self.provider not in PROVIDERS:
            raise ValueError(f"Unknown LLM provider {provider!r}; "
                             f"choose from {sorted(PROVIDERS)}")
        self.api_key = api_key
        self.model = model or PROVIDERS[self.provider]["model"]
        self.timeout = timeout
        self.max_repair_attempts = max_repair_attempts
        self._client = None

    # ---------------------------------------------------------------- setup
    def _get_client(self):
        """Lazily build the OpenAI-compatible client (needs a valid key)."""
        if self._client is None:
            from openai import OpenAI

            base_url = PROVIDERS[self.provider]["base_url"]
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)
        return self._client

    # ---------------------------------------------------------------- prompts
    @staticmethod
    def _user_prompt(context: str) -> str:
        return f"""Analyse the annual report below.

{context}

For every single metric and risk flag, you must include the exact page number \
and table ID from the provided context. If you cite a table, specify which row \
number. Your response will be parsed by machine, so adhere strictly to the JSON \
schema. Do not output anything except the JSON object.

Required JSON schema:
{{
  "company_name": str,
  "fiscal_year": str,
  "extracted_metrics": [
    {{"metric": str, "value": float|null, "unit": str, "citation": {{
      "page": int, "table_id": str|null, "row": int|null, "column": str|null}},
     "note": str|null}}
  ],
  "risk_flags": [
    {{"risk": str, "severity": "Low"|"Medium"|"High"|"Critical",
      "justification": str, "citation": {{
      "page": int, "table_id": str|null, "row": int|null, "column": str|null}},
     "note": str|null}}
  ],
  "executive_summary": str
}}
"""

    @staticmethod
    def _repair_prompt(malformed: str) -> str:
        return (
            "The previous response was not valid JSON. Fix the JSON below and "
            "return ONLY the corrected JSON object (no markdown fences). "
            "Keep every citation (page/table_id/row) intact.\n\n"
            f"Malformed JSON:\n{malformed}"
        )

    # ---------------------------------------------------------------- call
    def _chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        """Single chat completion call with a hard timeout."""
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            timeout=self.timeout,
        )
        return resp.choices[0].message.content or ""

    # ---------------------------------------------------------------- driver
    def analyse(self, context: str) -> FinalReport:
        """Run the analysis with JSON-repair retries; always return a valid report."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._user_prompt(context)},
        ]

        # attempt 0: normal call
        raw, error = self._safe_chat(messages)
        if raw is None:
            return self._error_report(f"LLM call failed: {error}")

        report, parse_error = self._parse(raw)
        if report is not None:
            return report

        # repair loop: feed the malformed JSON back with a fix prompt
        for attempt in range(1, self.max_repair_attempts + 1):
            logger.warning("JSON repair attempt %d: %s", attempt, parse_error)
            repair_messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": self._repair_prompt(raw)},
            ]
            raw2, error2 = self._safe_chat(repair_messages)
            if raw2 is None:
                return self._error_report(f"Repair attempt {attempt} failed: {error2}")
            report, parse_error2 = self._parse(raw2)
            if report is not None:
                return report
            parse_error = parse_error2
            raw = raw2

        return self._error_report(f"Could not produce valid JSON after repairs: {parse_error}")

    # ---------------------------------------------------------------- helpers
    def _safe_chat(self, messages: list[dict]) -> tuple[Optional[str], Optional[str]]:
        try:
            return self._chat(messages), None
        except Exception as exc:  # noqa: BLE001
            logger.exception("OpenAI call failed")
            return None, str(exc)

    @staticmethod
    def _parse(raw: str) -> tuple[Optional[FinalReport], Optional[str]]:
        """Parse + validate. Returns (report, error)."""
        try:
            cleaned = LLMClient._extract_json(raw)
            data = json.loads(cleaned)
            return FinalReport.model_validate(data), None
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Strip markdown fences / prose around the JSON payload."""
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        return text

    @staticmethod
    def _error_report(message: str) -> FinalReport:
        """Schema-valid fallback so the UI never crashes on a bad response."""
        logger.error("LLM analysis failed: %s", message)
        return FinalReport(
            company_name="Unknown",
            fiscal_year="Unknown",
            executive_summary="The analysis could not be completed due to a "
                              "processing error. Please review the raw output or retry.",
        )