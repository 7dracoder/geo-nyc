"""Pydantic schemas for document records and extraction output.

Persisted to disk in ``data/documents/extracted/{document_id}.json``,
also returned verbatim from the API. Keep additive — frontends and
Phase 4 chunking depend on the field set.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTED = "extracted"
    FAILED = "failed"


class ExtractedPage(BaseModel):
    """A single page worth of text plus light metadata."""

    model_config = ConfigDict(frozen=True)

    page: int = Field(..., ge=1)
    text: str
    char_count: int = Field(..., ge=0)
    is_empty: bool = False


class ExtractionResult(BaseModel):
    """Output of :class:`PDFExtractor.extract`."""

    document_id: str
    filename: str
    page_count: int
    pages: list[ExtractedPage]
    method: str = Field(default="pymupdf_text")
    char_count: int
    pages_with_text: int
    extracted_at: datetime


class DocumentRecord(BaseModel):
    """Full snapshot of an uploaded (and possibly extracted) document."""

    model_config = ConfigDict(use_enum_values=False)

    document_id: str
    filename: str
    sha256: str
    bytes: int
    media_type: str
    status: DocumentStatus
    uploaded_at: datetime
    extracted_at: datetime | None = None
    page_count: int | None = None
    char_count: int | None = None
    pages_with_text: int | None = None
    error: str | None = None
    raw_url: str | None = None
    extracted_url: str | None = None


class DocumentSummary(BaseModel):
    """Listing-friendly subset of :class:`DocumentRecord`."""

    document_id: str
    filename: str
    status: DocumentStatus
    bytes: int
    page_count: int | None = None
    uploaded_at: datetime
    extracted_at: datetime | None = None


__all__ = [
    "DocumentRecord",
    "DocumentStatus",
    "DocumentSummary",
    "ExtractedPage",
    "ExtractionResult",
]
