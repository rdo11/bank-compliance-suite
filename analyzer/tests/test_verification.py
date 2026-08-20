"""Tests for the shared citation verification engine (core/verification.py)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.verification import (
    MISSING_CITATION,
    NOT_FOUND,
    VALUE_MISMATCH,
    VERIFIED,
    INVALID_CITATION,
    VerificationEngine,
    parse_number_candidates,
    value_matches,
)


def make_engine():
    tables = [
        TableStub("TABLE_1", 3, ["Metric", "2024", "2023"],
                  [["Revenue", "1,000,000", "900,000"],
                   ["Net income", "60,000", "100,000"],
                   ["Total assets", "2,500,000", "2,200,000"]]),
        TableStub("TABLE_2", 5, ["Item", "2024"],
                  [["Staff costs", "1.234,56", ], ]),
    ]
    pages = {
        3: "Revenue increased to 1,000,000. Net income dropped 40%.",
        5: "Staff costs totalled 1.234,56 mio.",
    }
    return VerificationEngine(pages=pages.items(), tables=tables)


class TableStub:
    def __init__(self, table_id, page, columns, rows):
        self.table_id = table_id
        self.page = page
        self.columns = columns
        self.rows = rows


class TestNumberParsing(unittest.TestCase):
    def test_danish_comma_decimal(self):
        self.assertIn(1234.56, parse_number_candidates("1.234,56"))

    def test_english_dot_decimal(self):
        self.assertIn(1234.56, parse_number_candidates("1,234.56"))

    def test_thousands_dots(self):
        self.assertIn(1000000, parse_number_candidates("1.000.000"))

    def test_thousands_commas(self):
        self.assertIn(1000000, parse_number_candidates("1,000,000"))

    def test_parenthesised_negative(self):
        self.assertIn(-60000, parse_number_candidates("(60,000)"))

    def test_unit_suffix_stripped(self):
        self.assertIn(1234.56, parse_number_candidates("1.234,56 mio. DKK"))

    def test_value_match_scale_millions(self):
        # claimed in millions matches a full-amount cell
        self.assertTrue(value_matches(1234.56, "1,234,560,000"))
        self.assertTrue(value_matches(1.23456, "1.234,56"))

    def test_value_mismatch(self):
        self.assertFalse(value_matches(999, "1,000,000"))


class TestVerifyMetric(unittest.TestCase):
    def test_verified_with_column(self):
        eng = make_engine()
        m = MetricStub("Revenue", 1000000, CitationStub(page=3, table_id="TABLE_1", row=1, column="2024"))
        item = eng.verify_metric(m)
        self.assertEqual(item.status, VERIFIED)
        self.assertEqual(item.found_value, "1,000,000")

    def test_verified_danish_format(self):
        eng = make_engine()
        m = MetricStub("Staff costs", 1234.56, CitationStub(page=5, table_id="TABLE_2", row=1, column="2024"))
        item = eng.verify_metric(m)
        self.assertEqual(item.status, VERIFIED)

    def test_value_mismatch(self):
        eng = make_engine()
        m = MetricStub("Revenue", 500000, CitationStub(page=3, table_id="TABLE_1", row=1, column="2024"))
        item = eng.verify_metric(m)
        self.assertEqual(item.status, VALUE_MISMATCH)

    def test_wrong_row(self):
        eng = make_engine()
        m = MetricStub("Revenue", 1000000, CitationStub(page=3, table_id="TABLE_1", row=2, column="2024"))
        item = eng.verify_metric(m)
        self.assertNotEqual(item.status, VERIFIED)

    def test_table_not_found(self):
        eng = make_engine()
        m = MetricStub("Revenue", 1000000, CitationStub(page=3, table_id="TABLE_99", row=1))
        item = eng.verify_metric(m)
        self.assertEqual(item.status, INVALID_CITATION)

    def test_page_out_of_range(self):
        eng = make_engine()
        m = MetricStub("Revenue", 1000000, CitationStub(page=99, table_id="TABLE_1", row=1))
        item = eng.verify_metric(m)
        self.assertEqual(item.status, INVALID_CITATION)

    def test_missing_citation(self):
        eng = make_engine()
        m = MetricStub("Revenue", 1000000, None)
        item = eng.verify_metric(m)
        self.assertEqual(item.status, MISSING_CITATION)

    def test_no_row_number_search_by_label(self):
        eng = make_engine()
        m = MetricStub("Total assets", 2500000, CitationStub(page=3, table_id="TABLE_1", row=None))
        item = eng.verify_metric(m)
        self.assertEqual(item.status, VERIFIED)


class TestVerifyRisk(unittest.TestCase):
    def test_risk_verified(self):
        eng = make_engine()
        r = RiskStub("Declining profit margin", "High",
                     "Net income dropped 40%",
                     CitationStub(page=3, table_id="TABLE_1", row=2))
        item = eng.verify_risk(r)
        self.assertEqual(item.status, VERIFIED)

    def test_risk_not_found(self):
        eng = make_engine()
        r = RiskStub("Declining profit margin", "High",
                     "Revenue collapsed to zero",
                     CitationStub(page=3, table_id="TABLE_1", row=2))
        item = eng.verify_risk(r)
        self.assertEqual(item.status, NOT_FOUND)

    def test_risk_missing_citation(self):
        eng = make_engine()
        r = RiskStub("Declining profit margin", "High", "Net income dropped 40%", None)
        item = eng.verify_risk(r)
        self.assertEqual(item.status, MISSING_CITATION)


class TestReport(unittest.TestCase):
    def test_report_summary(self):
        eng = make_engine()
        report = ReportStub(
            metrics=[
                MetricStub("Revenue", 1000000, CitationStub(page=3, table_id="TABLE_1", row=1, column="2024")),
                MetricStub("Revenue", 999, CitationStub(page=3, table_id="TABLE_1", row=1, column="2024")),
            ],
            risks=[
                RiskStub("Declining profit margin", "High", "Net income dropped 40%",
                         CitationStub(page=3, table_id="TABLE_1", row=2)),
            ],
        )
        result = eng.verify_report(report)
        summary = result.summary
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["verified"], 2)
        self.assertEqual(summary["value_mismatch"], 1)
        self.assertFalse(summary["pass"])
        self.assertEqual(summary["verdict"], "REVIEW")


# --------------------------------------------------------------- small stubs
from dataclasses import dataclass


@dataclass
class CitationStub:
    page: int
    table_id: str = None
    row: int = None
    column: str = None

    def model_dump(self):
        return {"page": self.page, "table_id": self.table_id,
                "row": self.row, "column": self.column}


@dataclass
class MetricStub:
    metric: str
    value: float
    citation: object

    def model_dump(self):
        return {"metric": self.metric, "value": self.value,
                "citation": self.citation.model_dump() if self.citation else None}


@dataclass
class RiskStub:
    risk: str
    severity: str
    justification: str
    citation: object

    def model_dump(self):
        return {"risk": self.risk, "severity": self.severity,
                "justification": self.justification,
                "citation": self.citation.model_dump() if self.citation else None}


@dataclass
class ReportStub:
    metrics: list
    risks: list

    def __post_init__(self):
        self.extracted_metrics = self.metrics
        self.risk_flags = self.risks

    def to_dict(self):
        return {"extracted_metrics": [m.model_dump() for m in self.metrics],
                "risk_flags": [r.model_dump() for r in self.risks]}


if __name__ == "__main__":
    unittest.main()