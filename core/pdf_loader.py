"""
pdf_loader.py — extract text + tables from an annual-report PDF.

Strategy per page:
  1. Text: PyMuPDF (``fitz``) — page-by-page raw text.
  2. Tables: Camelot (``lattice`` first, then ``stream``). If Camelot
     produces nothing (e.g. not installed / no rules), fall back to
     Tabula. If both fail, the table region is captured as raw text
     lines so nothing is silently dropped.
  3. OCR: if a page has (almost) no text, the page is rendered to an
     image and passed through pytesseract so scanned reports still work.

Every table gets a stable 1-based page number (the ORIGINAL PDF page, not
the zero-based internal index) so citations in the LLM output are
human-verifiable.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ExtractedPage:
    """Text + tables found on one PDF page."""

    page_number: int  # 1-based page number in the original PDF
    text: str = ""
    tables: list[pd.DataFrame] = field(default_factory=list)
    ocr_used: bool = False


@dataclass
class ParsedDocument:
    """Everything extracted from one PDF."""

    path: str
    pages: list[ExtractedPage]
    ocr_fallback_used: bool = False

    @property
    def full_text(self) -> str:
        """Concatenated text of all pages, numbered so the LLM can cite."""
        blocks = []
        for p in self.pages:
            blocks.append(f"--- PAGE {p.page_number} ---\n{p.text}")
        return "\n\n".join(blocks)


class PDFLoader:
    """Loads text + tables from a PDF with layered fallbacks."""

    OCR_MIN_CHARS = 40  # pages with fewer chars are treated as scanned

    def __init__(self, ocr_enabled: bool = True) -> None:
        self.ocr_enabled = ocr_enabled
        self._camelot = self._import("camelot")
        self._tabula = self._import("tabula")
        self._tesseract = self._import("pytesseract") if ocr_enabled else None
        self._fitz = self._import("fitz")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _import(name: str):
        """Import a module, returning None if unavailable (graceful degrade)."""
        try:
            return __import__(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("module %r unavailable: %s", name, exc)
            return None

    # ------------------------------------------------------------ text / ocr
    def _extract_text(self, page) -> str:
        try:
            return page.get_text("text") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("PyMuPDF text extraction failed: %s", exc)
            return ""

    def _ocr_page(self, page, page_number: int) -> str:
        """Render the page to an image and OCR it (scanned-report fallback)."""
        if not self._tesseract or not self._fitz:
            return ""
        try:
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            from PIL import Image

            img = Image.open(io.BytesIO(img_bytes))
            text = self._tesseract.image_to_string(img, lang="eng")
            logger.info("OCR fallback used on page %d", page_number)
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR failed on page %d: %s", page_number, exc)
            return ""

    # --------------------------------------------------------------- tables
    def _camelot_tables(self, pdf_path: str, page: int) -> Optional[list[pd.DataFrame]]:
        """Camelot extraction: try both flavors, keep the richer result set.

        Some pages carry a small lattice table (e.g. an infographic box) next
        to the real financial table; picking the first non-empty result would
        silently drop the figures. So we rank candidate result-sets by total
        cell count and return the richest.
        """
        if not self._camelot:
            return None
        import inspect

        supported = inspect.signature(self._camelot.read_pdf).parameters
        quiet = {k: True for k in ("suppress_stdout", "silent") if k in supported}
        candidates: list[tuple[int, list[pd.DataFrame]]] = []
        for flavor in ("lattice", "stream"):
            try:
                tbls = self._camelot.read_pdf(
                    pdf_path,
                    pages=str(page),
                    flavor=flavor,
                    **quiet,
                )
                frames = [t.df for t in tbls if t.df.shape[0] > 1]
                if frames:
                    cells = sum(int(t.shape[0] * t.shape[1]) for t in frames)
                    candidates.append((cells, frames))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Camelot %s failed on page %d: %s", flavor, page, exc)
        return max(candidates, default=None)[1] if candidates else None

    def _tabula_tables(self, pdf_path: str, page: int) -> Optional[list[pd.DataFrame]]:
        """Tabula fallback for pages Camelot could not parse."""
        if not self._tabula:
            return None
        try:
            frames = self._tabula.read_pdf(
                pdf_path,
                pages=str(page),
                multiple_tables=True,
                stream=True,
                pandas_options={"header": None},
            )
            frames = [f for f in frames if f is not None and f.shape[0] > 0]
            return frames or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Tabula failed on page %d: %s", page, exc)
            return None

    def _raw_text_lines(self, page, page_number: int) -> list[pd.DataFrame]:
        """Last-resort: capture the page body as a single-column table."""
        lines = [ln.strip() for ln in self._extract_text(page).splitlines() if ln.strip()]
        if not lines:
            return []
        df = pd.DataFrame({"text": lines})
        df.attrs["source"] = f"raw-text-fallback page {page_number}"
        return [df]

    # --------------------------------------------------------------- driver
    def parse(self, pdf_source: str | Path | bytes, ocr_callback=None) -> ParsedDocument:
        """Parse a PDF from a path or raw bytes.

        ``ocr_callback`` (optional) is a callable(text, page_number) that
        lets the Streamlit UI stream progress; ignored if not provided.
        """
        if not self._fitz:
            raise RuntimeError("PyMuPDF (fitz) is required and unavailable")

        is_bytes = isinstance(pdf_source, bytes)
        if is_bytes:
            doc = self._fitz.open(stream=pdf_source, filetype="pdf")
            path = "<uploaded>"
        else:
            path = str(pdf_source)
            doc = self._fitz.open(path)

        pages: list[ExtractedPage] = []
        ocr_used_any = False

        try:
            for i in range(len(doc)):
                page = doc[i]
                page_number = i + 1  # original-PDF numbering (citation-safe)

                text = self._extract_text(page)
                if len(text.strip()) < self.OCR_MIN_CHARS and self._tesseract:
                    ocr_text = self._ocr_page(page, page_number)
                    if len(ocr_text) > len(text.strip()):
                        text = ocr_text
                        ocr_used_any = True

                ext = ExtractedPage(page_number=page_number, text=text, ocr_used=ocr_used_any)

                if is_bytes:
                    # Camelot/Tabula need a real file path; spill to temp file.
                    import tempfile

                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        tmp.write(pdf_source)
                        tmp_path = tmp.name
                    try:
                        frames = self._camelot_tables(tmp_path, page_number) or \
                                 self._tabula_tables(tmp_path, page_number) or \
                                 self._raw_text_lines(page, page_number)
                    finally:
                        Path(tmp_path).unlink(missing_ok=True)
                else:
                    frames = self._camelot_tables(path, page_number) or \
                             self._tabula_tables(path, page_number) or \
                             self._raw_text_lines(page, page_number)

                ext.tables = frames or []
                pages.append(ext)

                if ocr_callback is not None:
                    ocr_callback(text, page_number)
        finally:
            doc.close()

        return ParsedDocument(path=path, pages=pages, ocr_fallback_used=ocr_used_any)