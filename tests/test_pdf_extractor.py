"""Direct PyMuPDF extractor tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from geo_nyc.documents.pdf_extractor import PDFExtractor
from geo_nyc.exceptions import PDFExtractionError, UnsupportedDocumentError
from tests._pdf_helpers import make_pdf


def test_extract_returns_one_record_per_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "demo.pdf"
    make_pdf(pdf_path, ["Manhattan Schist outcrop", "Glacial till to 35 ft"])

    extractor = PDFExtractor()
    result = extractor.extract(
        pdf_path=pdf_path,
        document_id="d_abc123",
        filename="demo.pdf",
    )

    assert result.document_id == "d_abc123"
    assert result.page_count == 2
    assert len(result.pages) == 2
    assert result.pages_with_text == 2
    assert "Manhattan Schist" in result.pages[0].text
    assert "Glacial till" in result.pages[1].text
    assert result.method == "pymupdf_text"
    assert result.char_count > 0


def test_extract_marks_empty_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "with-empty.pdf"
    make_pdf(pdf_path, ["only the first page", ""])

    result = PDFExtractor().extract(
        pdf_path=pdf_path,
        document_id="d_empty01",
    )

    assert result.page_count == 2
    assert result.pages_with_text == 1
    assert result.pages[0].is_empty is False
    assert result.pages[1].is_empty is True


def test_missing_pdf_raises(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedDocumentError):
        PDFExtractor().extract(
            pdf_path=tmp_path / "nope.pdf",
            document_id="d_missing01",
        )


def test_corrupt_pdf_raises_pdf_extraction_error(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.pdf"
    bogus.write_bytes(b"%PDF-1.5 not really a pdf at all")
    with pytest.raises(PDFExtractionError):
        PDFExtractor().extract(pdf_path=bogus, document_id="d_bogus01")
