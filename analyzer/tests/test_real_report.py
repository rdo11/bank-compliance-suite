"""Integration test: run extraction + verification on the REAL annual report.

The real Novo Nordisk 2024 report is downloaded into sample_data/ by
scripts/download_sample.py. This test:

  1. extracts text + tables from a few pages of the real PDF
  2. fabricates a metric that points at an ACTUAL extracted cell
     (so it MUST verify) and one that points at a made-up value
     (so it MUST mismatch) — no LLM, no API key needed
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pdf_loader import PDFLoader
from core.table_processor import TableProcessor
from core.verification import VerificationEngine

try:
    from tests.test_verification import CitationStub, MetricStub, ReportStub
except ModuleNotFoundError:  # discovered without the analyzer dir on path
    from test_verification import CitationStub, MetricStub, ReportStub

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "sample_annual_report.pdf"


def _find_numeric_cell(tables):
    """Return (table, row_index, label, numeric_cell) for the first row in
    any table that contains a parseable number — skips header rows."""
    from core.verification import parse_number_candidates
    for table in tables:
        for idx, row in enumerate(table.rows):
            numeric = next((c for c in row if parse_number_candidates(c)), None)
            if numeric:
                label = next((c for c in row if not parse_number_candidates(c)), None) or row[0]
                return table, idx, label, numeric
    return None, None, None, None


class TestRealReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = PDFLoader(ocr_enabled=False)
        doc = loader.parse(SAMPLE)
        processor = TableProcessor()
        cls.pages = []
        cls.tables = []
        for page in doc.pages[:20]:
            cls.tables += processor.process(page.tables, page.page_number)
            cls.pages.append((page.page_number, page.text))
        cls.table, cls.row_idx, cls.label, cls.numeric = _find_numeric_cell(cls.tables)
        from core.verification import parse_number_candidates
        cls.claimed = min(parse_number_candidates(cls.numeric)) if cls.numeric else None

    def test_pdf_extracts(self):
        self.assertTrue(self.pages, "no pages extracted from the real PDF")
        self.assertTrue(self.tables, "no tables extracted from the real PDF")

    def test_fabricated_metric_verifies(self):
        """A metric built from a REAL cell must verify."""
        self.assertIsNotNone(self.numeric, "no numeric cell found in first 20 pages")
        metric = MetricStub(self.label, self.claimed,
                            CitationStub(page=self.table.page, table_id=self.table.table_id,
                                         row=self.row_idx + 1, column=self.table.columns[0]))
        engine = VerificationEngine(pages=self.pages, tables=self.tables)
        item = engine.verify_metric(metric)
        self.assertEqual(item.status, "verified",
                         f"real-cell metric failed: {item.detail}")

    def test_fabricated_metric_mismatches(self):
        """A metric pointing at a real cell but with a wrong value must fail."""
        self.assertIsNotNone(self.numeric)
        metric = MetricStub(self.label, -999999.0,
                            CitationStub(page=self.table.page, table_id=self.table.table_id,
                                         row=self.row_idx + 1, column=self.table.columns[0]))
        engine = VerificationEngine(pages=self.pages, tables=self.tables)
        item = engine.verify_metric(metric)
        self.assertEqual(item.status, "value_mismatch")

    def test_report_verifies_and_flags(self):
        """A full report with one good + one bad metric gets REVIEW verdict."""
        self.assertIsNotNone(self.numeric)
        report = ReportStub(
            metrics=[
                MetricStub(self.label, self.claimed,
                           CitationStub(page=self.table.page, table_id=self.table.table_id,
                                        row=self.row_idx + 1, column=self.table.columns[0])),
                MetricStub("Made up metric", 424242.0,
                           CitationStub(page=self.table.page, table_id=self.table.table_id,
                                        row=self.row_idx + 1, column=self.table.columns[0])),
            ],
            risks=[],
        )
        engine = VerificationEngine(pages=self.pages, tables=self.tables)
        result = engine.verify_report(report)
        summary = result.summary
        self.assertEqual(summary["verified"], 1)
        self.assertGreaterEqual(summary["value_mismatch"], 1)
        self.assertEqual(summary["verdict"], "REVIEW")


if __name__ == "__main__":
    unittest.main()