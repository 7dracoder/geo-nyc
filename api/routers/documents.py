"""Document ingestion endpoints.

Mirrors the geo-lm contract:

* ``POST /api/documents/upload`` — multipart PDF upload, idempotent on
  content hash.
* ``POST /api/documents/{id}/extract`` — run PyMuPDF text extraction
  and persist a per-page JSON record.
* ``GET /api/documents`` — list summaries.
* ``GET /api/documents/{id}`` — return full record.
* ``GET /api/documents/{id}/extraction`` — return cached extraction.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from api.schemas import DocumentListResponse
from geo_nyc.documents import (
    DocumentRecord,
    DocumentService,
    ExtractionResult,
    get_document_service,
)
from geo_nyc.exceptions import (
    DocumentError,
    DocumentNotFoundError,
    PDFExtractionError,
    UnsupportedDocumentError,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "/upload",
    response_model=DocumentRecord,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    service: DocumentService = Depends(get_document_service),
) -> DocumentRecord:
    try:
        content = await file.read()
        return service.upload(
            content=content,
            filename=file.filename or "document.pdf",
            media_type=file.content_type,
        )
    except UnsupportedDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except DocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.post("/{document_id}/extract", response_model=ExtractionResult)
async def extract_document(
    document_id: str,
    service: DocumentService = Depends(get_document_service),
) -> ExtractionResult:
    try:
        _, result = service.extract(document_id)
        return result
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except PDFExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except DocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    limit: int = 100,
    service: DocumentService = Depends(get_document_service),
) -> DocumentListResponse:
    items = service.list_documents(limit=limit)
    return DocumentListResponse(items=items, total=len(items))


@router.get("/{document_id}", response_model=DocumentRecord)
async def get_document(
    document_id: str,
    service: DocumentService = Depends(get_document_service),
) -> DocumentRecord:
    try:
        return service.get(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/{document_id}/extraction", response_model=ExtractionResult)
async def get_document_extraction(
    document_id: str,
    service: DocumentService = Depends(get_document_service),
) -> ExtractionResult:
    try:
        return service.get_extraction(document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except DocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
