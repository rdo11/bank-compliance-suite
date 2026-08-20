"""Debug one live claim: LLM said X at (page, table, row); what does the PDF really say there?"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pdf_loader import PDFLoader
from core.table_processor import TableProcessor
from core.verification import parse_number_candidates

PDF = Path(__file__).resolve().parents[1] / "sample_data" / "sample_annual_report.pdf"
result = json.loads((Path(__file__).resolve().parent / "live_result.json").read_text())

loader = PDFLoader(ocr_enabled=False)
doc = loader.parse(PDF.read_bytes())
proc = TableProcessor()
tables = []
for p in doc.pages:
    tables += proc.process(p.tables, p.page_number)
by_id = {t.table_id: t for t in tables}

for m in result["metrics"]:
    if m["metric"] not in ("Operating profit", "Gross margin", "Employees worldwide",
                           "Total assets", "Equity"):
        continue
    c = m["citation"]
    print(f"\n=== {m['metric']} claimed={m['value']} {m['unit']} "
          f"@ page={c['page']} {c['table_id']} row={c['row']} col={c['column']}")
    t = by_id.get(c.get("table_id"))
    if not t:
        print("  -> table_id NOT in extraction:", c.get("table_id"))
        continue
    print(f"  table {t.table_id} on page {t.page}, columns={t.columns[:6]} "
          f"({len(t.columns)} cols), rows={len(t.rows)}")
    if c.get("row") and 0 < c["row"] <= len(t.rows):
        row = t.rows[c["row"] - 1]
        print(f"  row[{c['row']}] = {row}")
        for cell in row:
            cands = parse_number_candidates(cell)
            if cands:
                print(f"    parse '{cell}' -> {sorted(cands)}")
    else:
        print(f"  row {c.get('row')} out of range")
    # search whole table for the claimed value
    claimed = m["value"]
    hits = []
    for ri, r in enumerate(t.rows):
        for ci, cell in enumerate(r):
            if claimed in parse_number_candidates(cell):
                hits.append((ri + 1, ci, cell))
    print(f"  claimed value {claimed} found at rows: {hits[:5]}")