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
    center_lng: float | None = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="Longitude (WGS84) of the click location. When provided with center_lat, "
        "the run builds a 1 km × 1 km model extent centred on this point.",
    )
    center_lat: float | None = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="Latitude (WGS84) of the click location.",
    )
    dsl_text: str | None = Field(
        default=None,
        description=(
            "Inline DSL text. When provided (and parses cleanly) it overrides "
            "both fixture and LLM-derived DSL for this run. Lets the 3D dock "
            "build a model directly from operator-supplied DSL."
        ),
    )


# --- DSL ------------------------------------------------------------------


class DSLParseRequest(BaseModel):
    """Body for ``POST /api/dsl/parse``."""

    text: str = Field(..., description="Raw DSL text to parse and validate.")


class DSLParseError(BaseModel):
    """One parse or validation issue, ready for inline rendering."""

    line: int | None = None
    column: int | None = None
    message: str
    severity: Literal["error", "warning"] = "error"


class DSLParseResponse(BaseModel):
    """Response for ``POST /api/dsl/parse``."""

    is_valid: bool
    rocks_count: int = 0
    depositions_count: int = 0
    erosions_count: int = 0
    intrusions_count: int = 0
    errors: list[DSLParseError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    "DSLParseError",
    "DSLParseRequest",
    "DSLParseResponse",
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
