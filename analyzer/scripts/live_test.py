"""Live end-to-end test: real Novo Nordisk PDF -> LLM -> verified report.

Replicates the exact steps of app.run_pipeline without the Streamlit UI.
Requires a live API key in the environment (loaded from analyzer/.env):
either DEEPSEEK_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY, with the
provider selected via LLM_PROVIDER (default: deepseek).
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root (core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # analyzer/ (models/)

logging.basicConfig(level=logging.INFO)

from dotenv import load_dotenv
from core.pdf_loader import PDFLoader
from core.table_processor import TableProcessor
from core.llm_client import LLMClient
from core.verification import VerificationEngine

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek").lower()
API_KEY = os.environ.get(f"{PROVIDER.upper()}_API_KEY", "")
assert API_KEY, f"{PROVIDER.upper()}_API_KEY is not set in analyzer/.env"
PDF = Path(__file__).resolve().parents[1] / "sample_data" / "sample_annual_report.pdf"


def main():
    start = time.time()
    loader = PDFLoader(ocr_enabled=True)
    doc = loader.parse(PDF.read_bytes())
    print(f"[1/4] extracted {len(doc.pages)} pages, ocr_fallback={doc.ocr_fallback_used}")

    processor = TableProcessor()
    tables = []
    for page in doc.pages:
        tables += processor.process(page.tables, page.page_number)
    context_parts = [doc.full_text]
    if tables:
        context_parts.append("=== EXTRACTED TABLES ===\n" + processor.build_context(tables))
    context = "\n\n".join(context_parts)
    print(f"[2/4] extracted {len(tables)} tables, context={len(context):,} chars")

    client = LLMClient(api_key=API_KEY, provider=PROVIDER)
    print(f"[3/4] calling {PROVIDER} ({client.model}) ...")
    report = client.analyse(context)
    t_llm = time.time() - start
    print(f"      LLM returned in {t_llm:.1f}s; truncated_context={client.last_truncated}")
    print(f"      company={report.company_name} year={report.fiscal_year} "
          f"metrics={len(report.extracted_metrics)} risks={len(report.risk_flags)}")

    engine = VerificationEngine(pages=[(p.page_number, p.text) for p in doc.pages],
                                tables=tables)
    verification = engine.verify_report(report)
    s = verification.summary
    print(f"[4/4] verification: {s}")
    print(f"      verdict = {s['verdict']}")

    print("\n----- per-metric detail -----")
    for item in verification.items:
        print(f"  {item.status:>16}  {item.label}")

    out = {
        "company_name": report.company_name,
        "fiscal_year": report.fiscal_year,
        "metrics": [m.model_dump() for m in report.extracted_metrics],
        "risks": [r.model_dump() for r in report.risk_flags],
        "executive_summary": report.executive_summary,
        "verification_summary": s,
        "llm_seconds": round(t_llm, 1),
        "pages": len(doc.pages),
        "tables": len(tables),
        "context_truncated": client.last_truncated,
    }
    dest = Path(__file__).resolve().parent / "live_result.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nFull result saved to {dest}")


if __name__ == "__main__":
    main()