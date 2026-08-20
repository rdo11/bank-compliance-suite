# Bank Compliance Suite

**AI document intelligence + GDPR-safe communication tooling for banking — with deterministic, machine-verified citations (EU AI Act readiness).**

Two production-quality modules sharing one core library:

| Module | What it does | Interface |
| --- | --- | --- |
| [`analyzer/`](analyzer/) | Annual-report PDF → structured financial analysis with **verified** citations (page / table / row) for every figure and risk flag | Streamlit web app |
| [`anonymizer/`](anonymizer/) | Clipboard PII redaction for Danish GDPR compliance — CPR, CVR, IBAN, accounts, cards, phones, emails, custom words; **100% offline** | Desktop app (Tkinter), packaged `.app`/`.exe` |

---

## Why this exists

A bank employee deals with two constant risks:

1. **Credit risk.** Reading a customer's annual report and summarizing it for a decision — where does a figure actually come from? Under the EU AI Act's explainability requirements (2026), an AI that produces financial conclusions must point at **auditable sources**.
2. **Data leakage.** Copy-pasting customer data between systems is where PII leaks. The fix must be zero-trust, offline, and leave an audit trail.

This suite solves both: the analyzer **verifies its own output against the source PDF** (no LLM in the verification loop), and the anonymizer redacts with checksum-validated rules before anything is shared.

## The verification engine (the hard part)

`core/verification.py` re-checks every citation the LLM emits:

```
LLM claims:  Revenue = 1,000,000 DKK @ page 12, TABLE_3, row 5, col 2024
Engine:      -> page 12 exists? table 3 on that page? row 5 in range?
             -> parse the cell with Danish/English number handling
               ("1.234,56" vs "1,234.56", "(60,000)" negatives, unit suffixes)
             -> does 1,000,000 match, allowing thousand/million/billion scaling?
             -> does the metric LABEL appear in that row?
             -> is the risk flag's justification text on the cited page?

Result:  ✅ verified | ❌ value mismatch | ⚠ label mismatch | ❌ not found | ❌ invalid citation
```

No LLM is involved in verification — it's deterministic, so it can be audited. The UI shows a **PASS / REVIEW** verdict per report, and the exported JSON includes the full verification trail.

**Real-world test:** the integration suite downloads the actual **Novo Nordisk 2024 annual report** (152 pages, 152 tables, ~647k chars of text) and proves that a metric fabricated from a real extracted cell verifies, while a wrong value fails.

## Tests

```
# shared core + analyzer (uses the real Novo Nordisk report)
cd analyzer && .venv/bin/python -m unittest discover -s tests -v

# anonymizer (38 tests, incl. ISO 7064 IBAN checksum + audit trail)
cd anonymizer && python3 -m unittest test_redactor -v
```

Every rule and check has a unit test: number parsing edge cases, IBAN mod-97 validation, Luhn card checks, CPR date plausibility, rule-ordering collisions, audit CSV export.

## Run it

### Analyzer (Streamlit)

```bash
cd analyzer
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # add your API key (DeepSeek / OpenAI / Gemini)
.venv/bin/streamlit run app.py
# or: python scripts/download_sample.py   -> fetches the real Novo Nordisk report
```

Provider-agnostic: **DeepSeek** (cheapest), **Gemini Flash Lite** (free tier) or **OpenAI GPT-4o** — all through one OpenAI-compatible client with JSON-repair retries and a 60s timeout.

Docker:

```bash
docker compose -f analyzer/docker-compose.yml up --build
```

### Anonymizer (desktop, offline)

```bash
cd anonymizer
python3 app.py                      # macOS / Linux
# Windows: double-click run.bat     (console hidden)
```

Build a double-clickable app (no Python needed on the target machine):

```bash
cd anonymizer
python3 -m venv .build-venv && .build-venv/bin/pip install pyinstaller pyperclip
.build-venv/bin/pyinstaller --noconfirm anonymizer.spec
# -> dist/Anonymizer.app (macOS) / dist/Anonymizer/Anonymizer.exe (Windows)
```

The packaged app was built and launch-tested on macOS.

## Security & compliance posture

- **Anonymizer:** zero network calls, standard library only (+ `pyperclip`). Redaction is checksum-validated (Luhn for cards, ISO 7064 mod-97 for IBANs, date plausibility for CPRs) to minimise false positives while never leaking a real identifier.
- **Audit trail:** every redaction is appended to `anonymizer_audit.jsonl` (thread-safe, ISO-8601 UTC) with per-category counts — exportable to CSV. It stores **no raw PII**.
- **Analyzer:** the AI output is validated against a strict Pydantic schema and every figure is re-checked against the PDF before you trust it.

## Project layout

```
bank-compliance-suite/
├── core/                      # shared library (both modules import this)
│   ├── pdf_loader.py          # PyMuPDF + Camelot + Tabula + OCR fallbacks
│   ├── table_processor.py     # cleaning, header flattening, Markdown, table IDs
│   ├── llm_client.py          # provider-agnostic LLM calls + JSON repair
│   ├── verification.py        # ★ deterministic citation verification engine
│   └── audit.py               # thread-safe JSONL audit trail + CSV export
├── analyzer/                  # Streamlit annual-report analyzer
│   ├── app.py                 # dual-column UI, 3 tabs (Summary/Verification/JSON)
│   ├── models/schemas.py      # strict output contract (Pydantic)
│   ├── utils/citation_formatter.py
│   ├── scripts/download_sample.py
│   ├── tests/                 # 24 tests incl. real Novo Nordisk integration
│   └── Dockerfile / docker-compose.yml
└── anonymizer/                # desktop GDPR clipboard anonymizer
    ├── app.py                 # Tkinter GUI, yellow change highlighting
    ├── redactor.py            # pure regex engine (stdlib only)
    ├── anonymizer.spec        # PyInstaller spec (packaged & launch-tested)
    ├── run.bat / run.command
    └── test_redactor.py       # 38 tests
```

## Licensing

MIT — see [LICENSE](LICENSE).

*Disclaimer: a personal portfolio project. Built against public documents; not affiliated with or endorsed by any bank.*