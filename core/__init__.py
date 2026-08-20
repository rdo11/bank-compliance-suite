"""bank-compliance-suite — shared core library.

Shared components used by the analyzer and anonymizer modules:
  * pdf_loader      — text + table extraction from PDFs (PyMuPDF/Camelot/Tabula/OCR)
  * table_processor — cleaning, header flattening, Markdown rendering
  * llm_client      — provider-agnostic LLM analysis (OpenAI/DeepSeek/Gemini)
  * verification    — deterministic citation verification (EU AI Act explainability)
  * audit           — lightweight, thread-safe audit trail (JSON + CSV export)
"""

__version__ = "1.0.0"