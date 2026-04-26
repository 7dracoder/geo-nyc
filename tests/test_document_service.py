"""Direct DocumentService tests (no HTTP layer)."""

from __future__ import annotations

import pytest

from geo_nyc.config import Settings
from geo_nyc.documents import DocumentService, DocumentStatus
from geo_nyc.exceptions import DocumentNotFoundError, UnsupportedDocumentError
from tests._pdf_helpers import make_pdf_bytes


@pytest.fixture
def service(isolated_settings: Settings) -> DocumentService:
    return DocumentService(settings=isolated_settings)


def test_upload_persists_pdf_and_record(
    service: DocumentService, isolated_settings: Settings
) -> None:
    content = make_pdf_bytes(["bedrock contact at 42 ft"])
    record = service.upload(content=content, filename="usgs report.pdf")

    assert record.status is DocumentStatus.UPLOADED
    assert record.document_id.startswith("d_")
    assert record.bytes == len(content)
    assert record.filename == "usgs_report.pdf"  # spaces sanitised

    raw_path = isolated_settings.documents_raw_dir / f"{record.document_id}.pdf"
    record_path = isolated_settings.documents_extracted_dir / f"{record.document_id}.record.json"
    assert raw_path.is_file()
    assert record_path.is_file()


def test_upload_is_idempotent_on_content_hash(service: DocumentService) -> None:
    content = make_pdf_bytes(["one page"])
    first = service.upload(content=content, filename="a.pdf")
    second = service.upload(content=content, filename="b.pdf")
    assert first.document_id == second.document_id
    assert first.uploaded_at == second.uploaded_at


def test_upload_rejects_non_pdf(service: DocumentService) -> None:
    with pytest.raises(UnsupportedDocumentError):
        service.upload(content=b"not a pdf at all", filename="x.txt")


def test_upload_rejects_empty_content(service: DocumentService) -> None:
    with pytest.raises(UnsupportedDocumentError):
        service.upload(content=b"", filename="x.pdf")


def test_extract_updates_record_and_persists_extraction(
    service: DocumentService, isolated_settings: Settings
) -> None:
    content = make_pdf_bytes(["Manhattan Schist", "till 25 ft"])
    record = service.upload(content=content, filename="demo.pdf")

    updated, result = service.extract(record.document_id)
    assert updated.status is DocumentStatus.EXTRACTED
    assert updated.page_count == 2
    assert result.page_count == 2
    assert any("Manhattan Schist" in p.text for p in result.pages)

    extraction_path = (
        isolated_settings.documents_extracted_dir / f"{record.document_id}.json"
    )
    assert extraction_path.is_file()


def test_extract_unknown_id_raises(service: DocumentService) -> None:
    with pytest.raises(DocumentNotFoundError):
        service.extract("d_does_not_exist")


def test_get_extraction_round_trip(service: DocumentService) -> None:
    content = make_pdf_bytes(["alpha"])
    record = service.upload(content=content, filename="a.pdf")
    service.extract(record.document_id)
    service._purge_cache()  # type: ignore[attr-defined]

    fetched = service.get_extraction(record.document_id)
    assert fetched.document_id == record.document_id
    assert fetched.pages[0].text.startswith("alpha")


def test_list_documents_returns_summaries(service: DocumentService) -> None:
    a = service.upload(content=make_pdf_bytes(["A"]), filename="a.pdf")
    b = service.upload(content=make_pdf_bytes(["B"]), filename="b.pdf")
    service.extract(b.document_id)

    summaries = service.list_documents()
    ids = {s.document_id for s in summaries}
    assert {a.document_id, b.document_id} <= ids
    extracted = next(s for s in summaries if s.document_id == b.document_id)
    assert extracted.status is DocumentStatus.EXTRACTED
    assert extracted.page_count == 1
