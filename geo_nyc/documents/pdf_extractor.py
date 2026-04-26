"""PyMuPDF-backed text extractor.

Wraps the bits of ``fitz`` we actually use behind a thin, mockable
class so the rest of the system can talk to a small, well-typed
interface. Empty pages (no embedded text) are preserved in the output
list so downstream chunkers can decide whether to ignore them or queue
them for OCR later.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz  # pymupdf

from geo_nyc.documents.schemas import ExtractedPage, ExtractionResult
from geo_nyc.exceptions import PDFExtractionError, UnsupportedDocumentError
from geo_nyc.logging import get_logger

_LOG = get_logger(__name__)


class PDFExtractor:
    """Extract per-page text from a PDF on disk."""

    def __init__(self, *, method: str = "pymupdf_text") -> None:
        self._method = method

    def extract(
        self,
        *,
        pdf_path: Path,
        document_id: str,
        filename: str | None = None,
    ) -> ExtractionResult:
        if not pdf_path.is_file():
            raise UnsupportedDocumentError(f"PDF not found at {pdf_path}")

        try:
            doc: Any = fitz.open(pdf_path)
        except Exception as exc:
            raise PDFExtractionError(f"PyMuPDF could not open {pdf_path.name}: {exc}") from exc

        try:
            pages: list[ExtractedPage] = []
            char_count = 0
            pages_with_text = 0
            for page_index, page in enumerate(doc):
                text = self._page_text(page)
                stripped = text.strip()
                is_empty = stripped == ""
                if not is_empty:
                    pages_with_text += 1
                char_count += len(text)
                pages.append(
                    ExtractedPage(
                        page=page_index + 1,
                        text=text,
                        char_count=len(text),
                        is_empty=is_empty,
                    )
                )
            page_count = len(pages)
        finally:
            doc.close()

        if page_count == 0:
            raise PDFExtractionError(
                f"PDF {filename or pdf_path.name} has zero pages."
            )

        result = ExtractionResult(
            document_id=document_id,
            filename=filename or pdf_path.name,
            page_count=page_count,
            pages=pages,
            method=self._method,
            char_count=char_count,
            pages_with_text=pages_with_text,
            extracted_at=datetime.now(UTC),
        )
        _LOG.info(
            "pdf_extracted",
            extra={
                "document_id": document_id,
                "page_count": page_count,
                "char_count": char_count,
                "pages_with_text": pages_with_text,
            },
        )
        return result

    def _page_text(self, page: Any) -> str:
        try:
            return str(page.get_text("text") or "")
        except Exception as exc:  # pragma: no cover - defensive
            raise PDFExtractionError(f"Could not extract text from page: {exc}") from exc


__all__ = ["PDFExtractor"]
