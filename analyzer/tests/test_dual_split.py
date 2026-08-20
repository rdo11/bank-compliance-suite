"""Tests for dual-table splitting and the audit-trail value hint."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.table_processor import TableProcessor, _split_dual
from core.verification import VerificationEngine, VALUE_MISMATCH

from tests.test_verification import CitationStub, MetricStub, ReportStub


def _dual_frame() -> pd.DataFrame:
    """Mimic a Camelot two-sides frame:
    label | 2023 | 2024 | Change || label | 2023 | 2024 | Change"""
    return pd.DataFrame([
        ["",    "2023",  "2024",  "Change", "Financial ratios", "2023", "2024", "Change"],
        ["Net sales", "232,261", "290,403", "25%", "Gross margin", "84.6%", "84.7%", "0.1pp"],
        ["Op. profit", "102,578", "128,339", "25%", "Net margin", "36.0%", "34.8%", "-1.2pp"],
    ])


class TestDualSplit(unittest.TestCase):
    def setUp(self):
        self.proc = TableProcessor()

    def test_split_detects_two_sides(self):
        parts = _split_dual(_dual_frame())
        self.assertEqual(len(parts), 2)
        # left keeps labels + years only
        self.assertEqual(parts[0].shape, (3, 4))
        self.assertEqual(parts[1].shape, (3, 4))

    def test_processed_rows_are_per_side(self):
        tables = self.proc.process([_dual_frame()], page=7)
        self.assertEqual(len(tables), 2)
        left, right = tables
        self.assertEqual(left.rows[0][0], "Net sales")
        self.assertEqual(left.rows[1][0], "Op. profit")
        self.assertEqual(right.rows[0][0], "Gross margin")
        self.assertEqual(right.rows[1][0], "Net margin")

    def test_normal_table_not_split(self):
        df = pd.DataFrame([
            ["Metric", "2023", "2024"],
            ["Net sales", "232,261", "290,403"],
        ])
        self.assertEqual(len(_split_dual(df)), 1)


class TestValueHint(unittest.TestCase):
    def _engine(self, tables):
        return VerificationEngine(pages=[(7, "page text")], tables=tables)

    def test_hint_reports_nearby_row(self):
        proc = TableProcessor()
        tables = proc.process([_dual_frame()], page=7)
        # cite Net sales (row 1) but with the 2024 value at a wrong row
        metric = MetricStub("Net sales", 290403.0,
                            CitationStub(page=7, table_id=tables[0].table_id,
                                         row=1, column="2024"))
        item = self._engine(tables).verify_metric(metric)
        self.assertEqual(item.status, "verified")

        # cite the SAME value but point at the row below the true one
        metric2 = MetricStub("Net sales", 290403.0,
                             CitationStub(page=7, table_id=tables[0].table_id,
                                          row=1, column="2023"))
        item2 = self._engine(tables).verify_metric(metric2)
        self.assertEqual(item2.status, VALUE_MISMATCH)
        self.assertIn("exact value at row", item2.detail)
        self.assertIn("col '2024'", item2.detail)

    def test_hint_absent_when_fabricated(self):
        proc = TableProcessor()
        tables = proc.process([_dual_frame()], page=7)
        metric = MetricStub("Net sales", 999999.0,
                            CitationStub(page=7, table_id=tables[0].table_id,
                                         row=1, column="2024"))
        item = self._engine(tables).verify_metric(metric)
        self.assertEqual(item.status, VALUE_MISMATCH)
        self.assertIn("not found anywhere", item.detail)


if __name__ == "__main__":
    unittest.main()