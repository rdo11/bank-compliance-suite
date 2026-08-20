"""
app.py — Streamlit frontend for the Annual Report Analyzer.

Layout:
  * Left column: PDF upload + "Process" button, progress bar with the
    three parsing steps, API-key warning if missing.
  * Right column: two tabs — "Executive Summary" (Markdown) and
    "Raw JSON Output" (pretty-printed) — plus a JSON export button.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# allow `python app.py` from the project root without a package install
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root -> core/

import streamlit as st
from dotenv import load_dotenv

from core.pdf_loader import PDFLoader
from core.table_processor import TableProcessor
from core.llm_client import LLMClient
from core.verification import VerificationEngine
from utils.citation_formatter import (
    format_metrics_table,
    format_risks_table,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("annual_report_analyzer")

APP_TITLE = "Annual Report Analyzer"
APP_SUBTITLE = "EU AI Act compliant — every figure & risk flag carries an exact citation."


# ------------------------------------------------------------------ pipeline
def run_pipeline(pdf_bytes: bytes, api_key: str, provider: str,
                 model: str = "", progress=None) -> dict:
    """Extract -> process -> analyse. Returns the FinalReport as a dict."""
    # Step 1: text
    if progress:
        progress.progress(0.1, text="Step 1: Extracting text")
    loader = PDFLoader(ocr_enabled=True)
    doc = loader.parse(pdf_bytes)

    # Step 2: tables
    if progress:
        progress.progress(0.45, text="Step 2: Extracting tables")
    processor = TableProcessor()
    all_tables = []
    for page in doc.pages:
        all_tables += processor.process(page.tables, page.page_number)

    context_parts = [doc.full_text]
    if all_tables:
        context_parts.append(
            "=== EXTRACTED TABLES ===\n" + processor.build_context(all_tables)
        )
    context = "\n\n".join(context_parts)

    # Step 3: AI analysis
    if progress:
        progress.progress(0.7, text="Step 3: AI Analysis (LLM call, up to 60s)")
    client = LLMClient(api_key=api_key, provider=provider, model=model)
    report = client.analyse(context)

    # Step 4: deterministic citation verification (no LLM)
    if progress:
        progress.progress(0.9, text="Step 4: Verifying citations against the PDF")
    engine = VerificationEngine(
        pages=[(p.page_number, p.text) for p in doc.pages],
        tables=all_tables,
    )
    verification = engine.verify_report(report)

    if progress:
        progress.progress(1.0, text="Done")

    return {
        "report": report.to_dict(),
        "verification": {
            "items": [item.to_dict() for item in verification.items],
            "summary": verification.summary,
        },
        "page_count": len(doc.pages),
        "table_count": len(all_tables),
        "ocr_fallback_used": doc.ocr_fallback_used,
    }


# ------------------------------------------------------------------------ UI
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption(APP_SUBTITLE)

left, right = st.columns([0.38, 0.62], gap="large")

with left:
    st.subheader("Input")
    uploaded = st.file_uploader(
        "Upload the annual report (PDF)",
        type=["pdf"],
        help="Danish annual report / årsrapport. Scanned PDFs are handled via OCR.",
    )

    provider = st.selectbox(
        "LLM provider",
        options=["openai", "deepseek", "gemini"],
        index=1,
        help="deepseek = ~1/30th of GPT-4o cost; gemini = free tier. "
             "All use the OpenAI-compatible protocol.",
    )

    # per-provider key from env, with a sensible default key slot
    default_env_key = {
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }[provider]
    api_key = st.text_input(
        f"API key ({default_env_key})",
        type="password",
        value=os.environ.get(default_env_key, ""),
        help=f"Stored in .env as {default_env_key}=... (never logged).",
    )
    model = st.text_input(
        "Model (leave blank for provider default)",
        value="",
        help="e.g. gpt-4o, deepseek-chat, gemini-2.0-flash-lite",
    )
    process = st.button("Process", type="primary", disabled=uploaded is None)

    if process:
        if not api_key:
            st.error(
                f"API key missing. Add {default_env_key}=... to the .env file "
                "or paste it above."
            )
        else:
            progress = st.progress(0.0, text="Ready")
            try:
                result = run_pipeline(
                    uploaded.getvalue(), api_key, provider, model, progress=progress
                )
                st.session_state["result"] = result
                st.success(
                    f"Parsed {result['page_count']} pages, "
                    f"{result['table_count']} tables"
                    + (" (OCR fallback was used for scanned pages)" if result["ocr_fallback_used"] else "")
                )
            except Exception as exc:  # noqa: BLE001
                progress.empty()
                st.error(f"Processing failed: {exc}")
                logger.exception("pipeline failed")

with right:
    st.subheader("Output")
    if "result" not in st.session_state:
        st.info("Upload a PDF and press Process — the analysis appears here.")
    else:
        result = st.session_state["result"]
        report = result["report"]
        verification = result["verification"]

        tab_summary, tab_verify, tab_json = st.tabs(
            ["Executive Summary", "Verification", "Raw JSON Output"]
        )

        with tab_summary:
            st.markdown(
                f"### {report['company_name']} — FY {report['fiscal_year']}"
            )
            st.markdown(report["executive_summary"])
            st.divider()

            st.markdown("#### Extracted metrics")
            st.markdown(format_metrics_table(report))

            st.markdown("#### Risk flags")
            st.markdown(format_risks_table(report))

            st.divider()
            st.caption("Citations are machine-verifiable (page / table ID / row). "
                       "See the Verification tab for the audit result.")

        with tab_verify:
            summary = verification["summary"]
            verdict = summary["verdict"]
            if verdict == "PASS":
                st.success(
                    f"**PASS** — {summary['verified']}/{summary['total']} "
                    "figures verified against the source PDF."
                )
            else:
                st.error(
                    f"**REVIEW REQUIRED** — {summary['value_mismatch'] + summary['not_found'] + summary['invalid_citation']} "
                    f"of {summary['total']} figures could NOT be verified against the source PDF."
                )
            st.markdown(
                "Deterministically re-checked every citation (page / table / "
                "row / column) against the extracted PDF — no LLM involved."
            )
            st.markdown(format_verification_table(verification["items"]))

        with tab_json:
            st.json(report, expanded=True)

        export_payload = {
            "report": report,
            "verification": verification,
        }
        export_name = (
            f"{report['company_name']}_{report['fiscal_year']}_risk_summary.json"
            .replace(" ", "_")
        )
        st.download_button(
            "Export JSON",
            data=json.dumps(export_payload, indent=2),
            file_name=export_name,
            mime="application/json",
        )