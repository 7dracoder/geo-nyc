"""Document ingestion: PDF upload + text extraction.

The :class:`DocumentService` is the public entry point. Routers should
hold a single instance via :func:`get_document_service`.
"""

from geo_nyc.documents.document_service import (
    DocumentService,
    get_document_service,
    reset_document_service,
)
from geo_nyc.documents.pdf_extractor import PDFExtractor
from geo_nyc.documents.schemas import (
    DocumentRecord,
    DocumentStatus,
    DocumentSummary,
    ExtractedPage,
    ExtractionResult,
)

__all__ = [
    "DocumentRecord",
    "DocumentService",
    "DocumentStatus",
    "DocumentSummary",
    "ExtractedPage",
    "ExtractionResult",
    "PDFExtractor",
    "get_document_service",
    "reset_document_service",
]
