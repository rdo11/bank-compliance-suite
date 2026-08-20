"""
table_processor.py — clean extracted DataFrames into citation-ready
Markdown tables with stable IDs.

Responsibilities:
  * assign a unique Table ID per table (TABLE_1, TABLE_2, ...)
  * remember the PDF page each table came from
  * flatten nested (multi-level) headers into single strings,
    e.g. ``2023 (Revenue)``
  * drop fully-empty rows/columns and de-duplicate repeated headers
  * convert the cleaned DataFrame to a Markdown string the LLM can cite
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

_EMPTY_TOKENS = {"", "nan", "none", "null", "-", "--", "–", "n/a", "na"}


def _clean_cell(value) -> str:
    """Normalise a single cell to a readable string."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in _EMPTY_TOKENS:
        return ""
    # collapse internal whitespace, keep unicode minus as '-'
    text = re.sub(r"\s+", " ", text)
    return text


def _is_empty_row(row: pd.Series) -> bool:
    return all(_clean_cell(v) == "" for v in row.tolist())


def _is_empty_col(col: pd.Series) -> bool:
    return all(_clean_cell(v) == "" for v in col.tolist())


def _flatten_header(index, col: str) -> str:
    """Flatten a MultiIndex header into one readable string.

    (2023, 'Revenue') -> '2023 (Revenue)'
    (None, 'Revenue')  -> 'Revenue'
    """
    levels = index if isinstance(index, tuple) else (index,)
    parts = []
    for level in levels:
        s = _clean_cell(level)
        if s:
            parts.append(s)
    if not parts:
        return _clean_cell(col) or f"Column_{len(parts)}"
    if len(parts) == 1:
        return parts[0]
    # outermost level first, nested in parens: "2023 (Revenue)"
    return f"{parts[0]} ({', '.join(parts[1:])})"


def _de_dup_columns(columns: list[str]) -> list[str]:
    """Make duplicate column names unique for Markdown sanity."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in columns:
        if c in seen:
            seen[c] += 1
            out.append(f"{c} ({seen[c]})")
        else:
            seen[c] = 0
            out.append(c)
    return out


_NUMERIC_RE = re.compile(
    r"^[\d.,%()+\-–\s]*[\d][\d.,%()+\-–]*(pp\.?|pp|pct\.?|%)?$", re.IGNORECASE)


def _is_numeric_cell(value) -> bool:
    """True when a cleaned cell is a pure number (incl. %, parens, dashes)."""
    s = _clean_cell(value)
    if not s or s.isalpha():
        return False
    return bool(_NUMERIC_RE.match(s))


def _split_dual(df: pd.DataFrame) -> list[pd.DataFrame]:
    """Split a two-sides-side-by-side Camelot frame into two logical tables.

    Financial-highlights pages often place two tables next to each other
    (e.g. "Financial performance" | "Financial ratios"). Camelot extracts
    them as one wide frame; the LLM then numbers rows per visual side while
    the verification engine numbers full rows — a systematic citation
    mismatch. Detecting the two label columns (one per side) lets us split
    the frame so row numbering stays consistent between the two.
    """
    if df.shape[1] < 5 or df.shape[0] < 2:
        return [df]

    def _label_score(col: int) -> float:
        vals = df.iloc[:, col]
        non_empty = sum(1 for v in vals if _clean_cell(v))
        numeric = sum(1 for v in vals if _is_numeric_cell(v))
        if non_empty == 0:
            return 0.0
        return (non_empty - numeric) / non_empty

    n_rows = df.shape[0]
    scores = [_label_score(c) for c in range(df.shape[1])]
    label_cols = [
        c for c in range(df.shape[1])
        if scores[c] >= 0.6
        and sum(1 for v in df.iloc[:, c] if _clean_cell(v)) >= 0.3 * n_rows
    ]

    if len(label_cols) < 2:
        return [df]

    first, second = label_cols[0], label_cols[1]
    if second - first < 2 or second + 1 >= df.shape[1]:
        return [df]

    def _numeric_run(lo: int, hi: int) -> bool:
        # tolerance: header cells like "Change" / "pp" labels lower the score
        return all(scores[c] <= 0.5 for c in range(lo, hi))

    # require numbers between the two label columns AND after the second one
    if not _numeric_run(first + 1, second) or not _numeric_run(second + 1, df.shape[1]):
        return [df]

    # reject matrix-like frames with more than two label clusters
    clusters = 1
    for i in range(1, len(label_cols)):
        if label_cols[i] - label_cols[i - 1] > 2:
            clusters += 1
    if clusters > 2:
        return [df]

    left = df.iloc[:, :second].reset_index(drop=True)
    right = df.iloc[:, second:].reset_index(drop=True)
    # reset column labels too (slices keep the original integer labels, which
    # defeats the RangeIndex check that promotes the first row to a header)
    left.columns = pd.RangeIndex(len(left.columns))
    right.columns = pd.RangeIndex(len(right.columns))
    logger.debug("Split dual table: %d cols -> left %d cols, right %d cols",
                 df.shape[1], left.shape[1], right.shape[1])
    return [left, right]


@dataclass
class ProcessedTable:
    """A cleaned table with its citation metadata."""

    table_id: str
    page: int
    columns: list[str]
    rows: list[list[str]]
    markdown: str = field(default="")
    source: str = "camelot"  # camelot | tabula | raw-text-fallback

    def __post_init__(self) -> None:
        self.markdown = self.to_markdown()

    def to_markdown(self) -> str:
        """Render the table as a Markdown pipe table."""
        header = "| " + " | ".join(self.columns) + " |"
        sep = "| " + " | ".join(["---"] * len(self.columns)) + " |"
        lines = [header, sep]
        for row in self.rows:
            # pad/truncate to column count
            cells = row + [""] * (len(self.columns) - len(row))
            cells = cells[: len(self.columns)]
            cells = [c.replace("|", "\\|") for c in cells]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)


class TableProcessor:
    """Owns the Table-ID counter and converts DataFrames to ProcessedTable."""

    def __init__(self) -> None:
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"TABLE_{self._counter}"

    # ---------------------------------------------------------------- public
    def process(
        self,
        frames: list[pd.DataFrame],
        page: int,
        source: str = "camelot",
    ) -> list[ProcessedTable]:
        """Clean + flatten a list of raw DataFrames from one page."""
        out: list[ProcessedTable] = []
        for df in frames:
            for part in _split_dual(df):
                cleaned = self._clean(part)
                if cleaned is None:
                    continue
                out.append(self._to_processed(cleaned, page, source))
        return out

    def build_context(self, tables: list[ProcessedTable]) -> str:
        """Join all tables into one markdown context block for the LLM."""
        blocks = []
        for t in tables:
            blocks.append(
                f"### {t.table_id} (page {t.page}, source: {t.source})\n{t.markdown}"
            )
        return "\n\n".join(blocks)

    # -------------------------------------------------------------- internals
    def _clean(self, df: pd.DataFrame) -> pd.DataFrame | None:
        """Drop empty rows/cols and set a flattened single-level header."""
        if df is None or df.shape[0] == 0:
            return None

        df = df.copy()
        # drop fully empty columns
        df = df.loc[:, ~df.apply(_is_empty_col, axis=0)].copy()
        # drop fully empty rows
        df = df[~df.apply(_is_empty_row, axis=1)].copy()
        if df.shape[0] == 0:
            return None

        # If the first row looks like a header and the frame has no header,
        # promote it. This handles Camelot's raw (header=None) output where
        # the first row is often the column names.
        if isinstance(df.columns, pd.RangeIndex) and df.columns[0] == 0:
            first = [_clean_cell(v) for v in df.iloc[0].tolist()]
            if any(first) and sum(1 for v in first if v) >= 1:
                df.columns = [f if f else f"Column_{i+1}" for i, f in enumerate(first)]
                df = df.iloc[1:].reset_index(drop=True)

            # Promote label-only sub-header rows (e.g. "Financial performance"
            # above "Net sales"). A row is a sub-header when it is sparse,
            # contains no digits, and the following row is numeric — keeping
            # these rows would shift every 1-based row citation by one.
            while df.shape[0] > 1:
                row0 = [_clean_cell(v) for v in df.iloc[0].tolist()]
                row1 = [_clean_cell(v) for v in df.iloc[1].tolist()]

                def _has_digit(row: list[str]) -> bool:
                    return any(any(ch.isdigit() for ch in cell) for cell in row)

                non_empty = sum(1 for v in row0 if v)
                sparse = non_empty * 2 < len(row0)
                if sparse and not _has_digit(row0) and _has_digit(row1):
                    logger.debug("Dropping sub-header row: %s", row0)
                    df = df.iloc[1:].reset_index(drop=True)
                else:
                    break

        # flatten MultiIndex headers -> ["2023 (Revenue)", ...]
        if isinstance(df.columns, pd.MultiIndex):
            flat = [_flatten_header(ix, str(i)) for i, ix in enumerate(df.columns)]
            df.columns = _de_dup_columns(flat)
        else:
            flat = [_clean_cell(c) or f"Column_{i+1}" for i, c in enumerate(df.columns)]
            df.columns = _de_dup_columns(flat)

        return df

    def _to_processed(self, df: pd.DataFrame, page: int, source: str) -> ProcessedTable:
        rows = [[_clean_cell(v) for v in row.tolist()] for _, row in df.iterrows()]
        return ProcessedTable(
            table_id=self._next_id(),
            page=page,
            columns=[str(c) for c in df.columns],
            rows=rows,
            source=source,
        )