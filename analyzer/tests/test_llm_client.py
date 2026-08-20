"""Unit tests for LLMClient context budgeting (no network calls)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llm_client import LLMClient, _TABLES_MARKER

BIG_TEXT = "A" * 100_000
_ROW = "| r | 1 | 2 |\n"
BIG_TABLES = "\n\n".join(
    f"### TABLE_{n} (page 1, source: camelot)\n{_ROW * 40}" for n in range(1, 30)
)
CONTEXT = BIG_TEXT + _TABLES_MARKER + BIG_TABLES


class TestContextBudget(unittest.TestCase):
    def setUp(self):
        self.client = LLMClient(api_key="test", provider="deepseek")

    def test_small_context_untouched(self):
        small = "small context"
        out = self.client._fit_context(small, 42000)
        self.assertEqual(out, small)
        self.assertFalse(self.client.last_truncated)

    def test_truncated_keeps_tables_and_text(self):
        out = self.client._fit_context(CONTEXT, 42000)
        self.assertLessEqual(len(out), 42000)
        self.assertIn(_TABLES_MARKER, out)
        self.assertIn("### TABLE_", out)          # at least one whole table kept
        self.assertGreaterEqual(out.count("### TABLE_"), 1)
        # tables must be kept whole: every header has all its 40 rows
        self.assertIn(f"{_ROW * 40}", out)
        self.assertTrue(out.startswith("A"))      # text front preserved

    def test_truncated_flag(self):
        self.client._fit_context(CONTEXT, 42000)
        self.assertTrue(self.client.last_truncated)


if __name__ == "__main__":
    unittest.main()