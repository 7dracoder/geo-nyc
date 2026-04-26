"""Document orchestrator.

Owns the on-disk and in-memory state for ``/api/documents`` requests:

* deterministic, content-derived ``document_id`` so uploading the same
  PDF twice is a no-op;
* an in-memory cache to avoid re-reading record JSON on hot paths;
* a clean separation between the small per-document *record* (status,
  sha256, byte count, …) and the large *extraction result* (page text).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from geo_nyc.config import Settings, get_settings
from geo_nyc.documents.pdf_extractor import PDFExtractor
from geo_nyc.documents.schemas import (
    DocumentRecord,
    DocumentStatus,
    DocumentSummary,
    ExtractionResult,
)
from geo_nyc.exceptions import (
    DocumentError,
    DocumentNotFoundError,
    PDFExtractionError,
    UnsupportedDocumentError,
)
from geo_nyc.logging import get_logger

_LOG = get_logger(__name__)
_PDF_MAGIC = b"%PDF-"
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentService:
    """Single point of contact for document persistence + extraction."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        extractor: PDFExtractor | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._extractor = extractor or PDFExtractor()
        self._records: dict[str, DocumentRecord] = {}
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Mutating operations
    # ------------------------------------------------------------------

    def upload(
        self,
        *,
        content: bytes,
        filename: str,
        media_type: str | None = None,
    ) -> DocumentRecord:
        if not content:
            raise UnsupportedDocumentError("Uploaded file is empty.")
        if not content.startswith(_PDF_MAGIC):
            raise UnsupportedDocumentError(
                "Uploaded file does not look like a PDF (missing %PDF- header)."
            )

        sha = hashlib.sha256(content).hexdigest()
        document_id = self._build_document_id(sha)
        safe_name = self._sanitise_filename(filename)

        raw_path = self._raw_path(document_id)
        if raw_path.exists() and raw_path.read_bytes()[:5] == _PDF_MAGIC:
            existing = self._load_record(document_id)
            if existing is not None:
                _LOG.info(
                    "document_upload_idempotent",
                    extra={"document_id": document_id, "doc_filename": safe_name},
                )
                return existing

        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(content)

        record = DocumentRecord(
            document_id=document_id,
            filename=safe_name,
            sha256=sha,
            bytes=len(content),
            media_type=media_type or "application/pdf",
            status=DocumentStatus.UPLOADED,
            uploaded_at=datetime.now(UTC),
        )
        self._save_record(record)
        _LOG.info(
            "document_uploaded",
            extra={"document_id": document_id, "bytes": len(content)},
        )
        return record

    def extract(self, document_id: str) -> tuple[DocumentRecord, ExtractionResult]:
        record = self._load_record(document_id)
        if record is None:
            raise DocumentNotFoundError(f"No document with id={document_id!r}")

        raw_path = self._raw_path(document_id)
        if not raw_path.is_file():
            raise DocumentNotFoundError(
                f"Raw PDF for {document_id!r} is missing on disk."
            )

        try:
            result = self._extractor.extract(
                pdf_path=raw_path,
                document_id=document_id,
                filename=record.filename,
            )
        except PDFExtractionError as exc:
            failed = record.model_copy(
                update={
                    "status": DocumentStatus.FAILED,
                    "error": str(exc),
                }
            )
            self._save_record(failed)
            raise

        extraction_path = self._extraction_path(document_id)
        extraction_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        updated = record.model_copy(
            update={
                "status": DocumentStatus.EXTRACTED,
                "extracted_at": result.extracted_at,
                "page_count": result.page_count,
                "char_count": result.char_count,
                "pages_with_text": result.pages_with_text,
                "error": None,
            }
        )
        self._save_record(updated)
        return updated, result

    # ------------------------------------------------------------------
    # Read-only operations
    # ------------------------------------------------------------------

    def get(self, document_id: str) -> DocumentRecord:
        record = self._load_record(document_id)
        if record is None:
            raise DocumentNotFoundError(f"No document with id={document_id!r}")
        return record

    def get_extraction(self, document_id: str) -> ExtractionResult:
        path = self._extraction_path(document_id)
        if not path.is_file():
            raise DocumentNotFoundError(
                f"No extraction available for {document_id!r}; call /extract first."
            )
        try:
            return ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DocumentError(
                f"Extraction file for {document_id!r} is corrupt: {exc}"
            ) from exc

    def list_documents(self, limit: int = 100) -> list[DocumentSummary]:
        seen: dict[str, DocumentRecord] = {}

        record_dir = self._settings.documents_extracted_dir
        if record_dir.is_dir():
            for path in record_dir.glob("*.record.json"):
                doc_id = path.name.removesuffix(".record.json")
                rec = self._load_record(doc_id)
                if rec is not None:
                    seen[doc_id] = rec

        # Stragglers: a raw PDF on disk with no record (e.g. dropped in
        # manually). Build a minimal record so the listing still works.
        raw_dir = self._settings.documents_raw_dir
        if raw_dir.is_dir():
            for path in raw_dir.glob("*.pdf"):
                doc_id = path.stem
                if doc_id in seen:
                    continue
                try:
                    content = path.read_bytes()
                except OSError:
                    continue
                if not content.startswith(_PDF_MAGIC):
                    continue
                rec = DocumentRecord(
                    document_id=doc_id,
                    filename=path.name,
                    sha256=hashlib.sha256(content).hexdigest(),
                    bytes=len(content),
                    media_type="application/pdf",
                    status=DocumentStatus.UPLOADED,
                    uploaded_at=datetime.fromtimestamp(
                        path.stat().st_mtime, tz=UTC
                    ),
                )
                self._save_record(rec)
                seen[doc_id] = rec

        ordered = sorted(seen.values(), key=lambda r: r.uploaded_at, reverse=True)
        return [self._summary(r) for r in ordered[:limit]]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_document_id(self, sha: str) -> str:
        return f"d_{sha[:12]}"

    def _sanitise_filename(self, filename: str) -> str:
        base = Path(filename).name or "document.pdf"
        cleaned = _FILENAME_SAFE.sub("_", base).strip("._-")
        return cleaned or "document.pdf"

    def _raw_path(self, document_id: str) -> Path:
        return self._settings.documents_raw_dir / f"{document_id}.pdf"

    def _record_path(self, document_id: str) -> Path:
        return self._settings.documents_extracted_dir / f"{document_id}.record.json"

    def _extraction_path(self, document_id: str) -> Path:
        return self._settings.documents_extracted_dir / f"{document_id}.json"

    def _load_record(self, document_id: str) -> DocumentRecord | None:
        with self._lock:
            cached = self._records.get(document_id)
        if cached is not None:
            return cached

        path = self._record_path(document_id)
        if not path.is_file():
            return None
        try:
            record = DocumentRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            _LOG.exception("document_record_corrupt", extra={"document_id": document_id})
            return None

        with self._lock:
            self._records[document_id] = record
        return record

    def _save_record(self, record: DocumentRecord) -> None:
        path = self._record_path(record.document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        with self._lock:
            self._records[record.document_id] = record

    def _summary(self, record: DocumentRecord) -> DocumentSummary:
        return DocumentSummary(
            document_id=record.document_id,
            filename=record.filename,
            status=record.status,
            bytes=record.bytes,
            page_count=record.page_count,
            uploaded_at=record.uploaded_at,
            extracted_at=record.extracted_at,
        )

    # Test helpers -----------------------------------------------------

    def _purge_cache(self) -> None:
        with self._lock:
            self._records.clear()


# ----------------------------------------------------------------------
# Module-level accessor
# ----------------------------------------------------------------------


_DEFAULT_SERVICE: DocumentService | None = None


def get_document_service() -> DocumentService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = DocumentService()
    return _DEFAULT_SERVICE


def reset_document_service() -> None:
    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = None


__all__ = [
    "DocumentService",
    "get_document_service",
    "reset_document_service",
]
