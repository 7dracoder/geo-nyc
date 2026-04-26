"""Pydantic schemas shared across API routers.

Keeping every public response type in one module makes it easy to
generate the OpenAPI document and to point the frontend team at a
single source of truth.

The run-related schemas live in :mod:`geo_nyc.runs.manifest` because
they are also persisted to disk; we re-export them here so OpenAPI
clients see one consistent location.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from geo_nyc.documents.schemas import (
    DocumentRecord,
    DocumentStatus,
    DocumentSummary,
    ExtractedPage,
    ExtractionResult,
)
from geo_nyc.runs.manifest import (
    Artifact,
    RunManifest,
    RunStatus,
    ValidationIssue,
    ValidationReportPayload,
)

# --- Health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    use_fixtures: bool
    enable_gempy: bool


class LLMHealthResponse(BaseModel):
    status: Literal["ok", "down", "degraded"]
    provider: str
    base_url: str
    model: str | None = None
    model_pulled: bool | None = None
    available_models: list[str] = Field(default_factory=list)
    detail: str | None = None


# --- Run ------------------------------------------------------------------


class RunRequest(BaseModel):
    """Body for ``POST /api/run``.

    All fields are optional so the demo can fire a one-line request and
    let the backend pick fixture-mode defaults.
    """

    document_id: str | None = Field(
        default=None,
        description="ID of an already-extracted document. If omitted, fixture mode is used.",
    )
    use_fixtures: bool | None = Field(
        default=None,
        description="Force fixture mode for this single run, overriding the env default.",
    )
    fixture_name: str | None = Field(
        default=None,
        description="Explicit fixture bundle name (defaults to 'nyc_demo').",
    )
    model_name: str | None = Field(
        default=None,
        description="Optional logical model name (recorded in the manifest, no behaviour today).",
    )
    use_llm: bool = Field(
        default=False,
        description=(
            "When true (and document_id is provided) the run will call Ollama to "
            "produce a structured extraction with a repair loop. False keeps the "
            "fixture-only behaviour used in tests and the offline demo."
        ),
    )
    top_k_chunks: int | None = Field(
        default=None,
        ge=1,
        le=32,
        description="Optional override for the number of top-ranked chunks fed to the LLM.",
    )


class RunListResponse(BaseModel):
    """Wrapper for ``GET /api/runs`` to keep the door open for pagination."""

    items: list[RunManifest] = Field(default_factory=list)
    total: int = 0


class DocumentListResponse(BaseModel):
    """Wrapper for ``GET /api/documents``."""

    items: list[DocumentSummary] = Field(default_factory=list)
    total: int = 0


__all__ = [
    "Artifact",
    "DocumentListResponse",
    "DocumentRecord",
    "DocumentStatus",
    "DocumentSummary",
    "ExtractedPage",
    "ExtractionResult",
    "HealthResponse",
    "LLMHealthResponse",
    "RunListResponse",
    "RunManifest",
    "RunRequest",
    "RunStatus",
    "ValidationIssue",
    "ValidationReportPayload",
]
