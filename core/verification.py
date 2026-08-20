"""
verification.py — deterministic citation verification for AI-extracted figures.

The whole point of the analyzer is "every figure is machine-verifiable". The
LLM *claims* a figure sits on page 12, table 3, row 5. This engine checks that
claim deterministically:

  * page/table/row/column exist and are in range
  * the metric's value actually appears at the cited location (with
    Danish/English number handling: '1.234,56' vs '1,234.56')
  * the metric's label appears in the cited row
  * a risk flag's justification text appears on the cited page

Output is a ``VerificationReport`` with per-item statuses and an overall
pass/review summary. No LLM, no network — pure pandas-free logic.

The input contract is duck-typed so it works on the analyzer's
``ProcessedTable`` objects (needs .table_id, .page, .columns, .rows).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ------------------------------------------------------------------ statuses
VERIFIED = "verified"
VALUE_MISMATCH = "value_mismatch"
LABEL_MISMATCH = "label_mismatch"
NOT_FOUND = "not_found"
INVALID_CITATION = "invalid_citation"
MISSING_CITATION = "missing_citation"

_UNIT_WORDS = {
    "dkk", "kr", "kr.", "eur", "usd", "us$", "$", "€", "%", "pct",
    "million", "mio", "mio.", "m", "millions", "billion", "mrd", "mrd.",
    "thousand", "k", "mn", "bn",
}

_NUMBER_RE = re.compile(r"-?\d[\d\s.,'’]*\d|\d")


def parse_number_candidates(text: str) -> list[float]:
    """Return plausible float values contained in a cell string.

    Handles Danish (comma decimal, dot thousands) and English (dot decimal,
    comma thousands) conventions, parenthesised negatives and trailing unit
    words. Ambiguous cases yield multiple candidates; the caller picks the
    one that matches the claimed figure.
    """
    raw = text.strip()
    if not raw:
        return []

    # pull out the leading sign of the first number run
    sign = -1.0 if raw.lstrip().startswith("-") or raw.startswith("(") else 1.0
    raw = raw.strip("() ")

    # strip unit words / currency suffixes (e.g. "1.234,56 mio. DKK")
    for word in sorted(_UNIT_WORDS, key=len, reverse=True):
        raw = re.sub(rf"\s*{re.escape(word)}\b", "", raw, flags=re.IGNORECASE)
    raw = raw.strip()

    matches = _NUMBER_RE.findall(raw)
    candidates: set[float] = set()
    for m in matches:
        m = m.replace(" ", "").replace("'", "").replace("’", "")
        if not m or m in ("-", "."):
            continue
        for cand in _interpretations(m):
            candidates.add(sign * cand)

    return sorted(candidates, key=abs)


def _interpretations(num: str) -> set[float]:
    """All sensible interpretations of one digit/dot/comma string."""
    out: set[float] = set()
    clean = num.replace(",", "").replace(".", "")
    if clean.lstrip("-").isdigit():
        try:
            out.add(float(clean) * (-1 if num.startswith("-") else 1))
        except ValueError:
            pass

    # dot as decimal (English) — "1,234.56"
    if "." in num and num.count(".") == 1:
        cand = num.replace(",", "")
        try:
            out.add(float(cand))
        except ValueError:
            pass

    # comma as decimal (Danish) — "1.234,56"
    if "," in num and num.count(",") == 1:
        cand = num.replace(".", "").replace(",", ".")
        try:
            out.add(float(cand))
        except ValueError:
            pass

    return out


def _close(a: float, b: float, rel: float = 1e-3, abs_: float = 0.5) -> bool:
    return abs(a - b) <= max(abs_ , rel * max(abs(a), abs(b)))


def value_matches(claimed: float, cell_text: str,
                  allow_scale: bool = True) -> bool:
    """True if `claimed` plausibly equals the number inside `cell_text`.

    ``allow_scale`` also tries thousand/million/billion scale factors so a
    figure stated in millions ("1.234" mio.) can match a cell of 1,234,000,000.
    Scaled matches require an (essentially) exact conversion.
    """
    candidates = parse_number_candidates(cell_text)
    if not candidates:
        return False
    for c in candidates:
        if _close(claimed, c, rel=1e-3, abs_=0.5):
            return True
        if allow_scale:
            for s in (1e3, 1e6, 1e9):
                if _close(claimed, c / s, rel=1e-6, abs_=1e-3):
                    return True
                if _close(claimed, c * s, rel=1e-6, abs_=1e-3):
                    return True
    return False


# ------------------------------------------------------------- table index
@dataclass
class TableLike:
    """Minimal duck-typed view of a table (works with ProcessedTable)."""

    table_id: str
    page: int
    columns: list[str]
    rows: list[list[str]]


# ------------------------------------------------------------------ results
@dataclass
class VerificationItem:
    """Result of checking one metric or risk flag against the source PDF."""

    kind: str            # "metric" | "risk"
    label: str           # metric name / risk name
    status: str
    claimed_value: Optional[str] = None
    found_value: Optional[str] = None
    citation: Optional[dict] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "claimed_value": self.claimed_value,
            "found_value": self.found_value,
            "citation": self.citation,
            "detail": self.detail,
        }


@dataclass
class VerificationReport:
    """Aggregate result: every check plus a pass/review verdict."""

    items: list[VerificationItem] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        counts = {VERIFIED: 0, VALUE_MISMATCH: 0, LABEL_MISMATCH: 0,
                  NOT_FOUND: 0, INVALID_CITATION: 0, MISSING_CITATION: 0}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        bad = counts[VALUE_MISMATCH] + counts[NOT_FOUND] + counts[INVALID_CITATION]
        return {
            **counts,
            "total": len(self.items),
            "pass": bad == 0,
            "verdict": "PASS" if bad == 0 else "REVIEW",
        }


class VerificationEngine:
    """Check a report's citations against the extracted PDF tables/text."""

    def __init__(self, pages: Iterable[tuple[int, str]],
                 tables: Iterable[TableLike]) -> None:
        """
        pages: iterable of (page_number, page_text)
        tables: iterable of table-like objects for every page
        """
        self._page_text = {pn: txt for pn, txt in pages}
        self._tables: dict[str, TableLike] = {}
        self._tables_by_page: dict[int, list[TableLike]] = {}
        for t in tables:
            self._tables[t.table_id] = t
            self._tables_by_page.setdefault(t.page, []).append(t)

    # ------------------------------------------------------------ public API
    def verify_metric(self, metric) -> VerificationItem:
        c = metric.citation
        label = metric.metric
        if c is None or c.page is None:
            return VerificationItem(
                "metric", label, MISSING_CITATION,
                claimed_value=self._num(metric.value),
                citation=None, detail="No citation provided by the model.")

        page_ok = c.page in self._page_text
        if not page_ok:
            return self._invalid(label, metric, "Page %s does not exist." % c.page)

        table = self._find_table(c)
        if table is None:
            return self._invalid(label, metric, "Table %r not found on page %d."
                                 % (c.table_id, c.page))

        row_idx = self._row_index(table, c.row, label)
        if row_idx is None:
            return self._invalid(label, metric,
                                 "Row %s not found in %s." % (c.row, table.table_id))

        row_cells = table.rows[row_idx]
        found_text = " | ".join(c for c in row_cells if c)

        # value check: locate the column if given, else scan the whole row
        if c.column is not None:
            cell = self._cell_at(table, row_idx, c.column)
            value_ok = (metric.value is None) or value_matches(
                float(metric.value), cell or "")
            value_str = cell
        else:
            value_ok = (metric.value is None) or any(
                value_matches(float(metric.value), c2) for c2 in row_cells if c2
            )
            value_str = found_text

        # label check: metric name words should appear in the row
        label_ok = self._label_in_row(label, row_cells)

        if not value_ok:
            return VerificationItem(
                "metric", label, VALUE_MISMATCH,
                claimed_value=self._num(metric.value),
                found_value=value_str,
                citation=c.model_dump(),
                detail=f"Value {metric.value} not found at cited location "
                       f"(found '{value_str}').")

        if not label_ok:
            return VerificationItem(
                "metric", label, LABEL_MISMATCH,
                claimed_value=self._num(metric.value),
                found_value=value_str,
                citation=c.model_dump(),
                detail=f"Metric name '{label}' not found in cited row.")

        return VerificationItem(
            "metric", label, VERIFIED,
            claimed_value=self._num(metric.value),
            found_value=value_str,
            citation=c.model_dump(),
            detail="Figure and label confirmed at cited location.")

    def verify_risk(self, risk) -> VerificationItem:
        c = risk.citation
        label = risk.risk
        if c is None or c.page is None:
            return VerificationItem(
                "risk", label, MISSING_CITATION,
                citation=None, detail="No citation provided by the model.")

        if c.page not in self._page_text:
            return VerificationItem(
                "risk", label, INVALID_CITATION,
                citation=c.model_dump(),
                detail="Page %s does not exist." % c.page)

        # justification must appear on the cited page (or its tables)
        search_text = self._page_text.get(c.page, "")
        for t in self._tables_by_page.get(c.page, []):
            search_text += "\n" + "\n".join(" ".join(r) for r in t.rows)

        needle = (risk.justification or "").strip()
        if needle and needle.lower() in search_text.lower():
            return VerificationItem(
                "risk", label, VERIFIED,
                citation=c.model_dump(),
                detail="Justification found on cited page.")
        if not needle:
            return VerificationItem(
                "risk", label, MISSING_CITATION,
                citation=c.model_dump(),
                detail="No justification text to verify.")
        return VerificationItem(
            "risk", label, NOT_FOUND,
            citation=c.model_dump(),
            detail="Justification not found on cited page.")

    def verify_report(self, report) -> VerificationReport:
        items = [self.verify_metric(m) for m in getattr(report, "extracted_metrics", [])]
        items += [self.verify_risk(r) for r in getattr(report, "risk_flags", [])]
        return VerificationReport(items=items)

    # ------------------------------------------------------------- internals
    def _num(self, value) -> Optional[str]:
        return None if value is None else f"{value:g}"

    def _find_table(self, c) -> Optional[TableLike]:
        if c.table_id:
            return self._tables.get(c.table_id)
        # no table id -> any table on the page
        return (self._tables_by_page.get(c.page) or [None])[0]

    def _row_index(self, table: TableLike, row: Optional[int], label: str) -> Optional[int]:
        if not table.rows:
            return None
        if row is not None:
            idx = row - 1  # 1-based
            return idx if 0 <= idx < len(table.rows) else None
        # search for the row whose cells mention the label
        for i, r in enumerate(table.rows):
            if any(label.lower() in cell.lower() for cell in r):
                return i
        return None

    def _cell_at(self, table: TableLike, row_idx: int,
                 column: Optional[str]) -> Optional[str]:
        row = table.rows[row_idx]
        if not column:
            return next((c for c in row if c), None)
        # map column header to index (fuzzy, case-insensitive)
        for i, header in enumerate(table.columns):
            if column.lower() in header.lower() or header.lower() in column.lower():
                if i < len(row):
                    return row[i]
        return next((c for c in row if c), None)

    def _label_in_row(self, label: str, row_cells: list[str]) -> bool:
        words = [w for w in re.split(r"\W+", label.lower()) if w and w not in
                 {"the", "a", "an", "of", "for", "in", "and", "as", "per"}]
        if not words:
            return True
        joined = " ".join(row_cells).lower()
        return sum(1 for w in words if w in joined) >= max(1, len(words) // 2)

    def _invalid(self, label: str, metric, detail: str) -> VerificationItem:
        c = metric.citation
        return VerificationItem(
            "metric", label, INVALID_CITATION,
            claimed_value=self._num(metric.value),
            citation=c.model_dump() if c else None,
            detail=detail)


def verify_report_report(report, pages, tables) -> VerificationReport:
    """Convenience wrapper for a one-shot check."""
    return VerificationEngine(pages, tables).verify_report(report)