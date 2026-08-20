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
            cleaned = self._clean(df)
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