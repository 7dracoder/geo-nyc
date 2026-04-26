"""Pydantic schemas for run state, manifest, and validation report.

These structures are persisted to disk under ``data/runs/{run_id}/``
and surfaced verbatim on ``GET /api/run/{run_id}``. Treat them as a
public API contract — additive changes only.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    """Lifecycle status surfaced to API clients."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Artifact(BaseModel):
    """A single produced file the frontend can fetch."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(..., description="Logical artifact kind, e.g. mesh|field|extraction|dsl|report")
    filename: str
    relative_path: str = Field(..., description="Path relative to data/runs/{run_id}.")
    url: str = Field(..., description="Absolute or root-relative URL the frontend can fetch.")
    bytes: int
    media_type: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    severity: str
    message: str
    location: str | None = None


class ValidationReportPayload(BaseModel):
    """Frontend-facing wrapper for :class:`ValidationReport`."""

    is_valid: bool
    error_count: int
    warning_count: int
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class RunManifest(BaseModel):
    """Full snapshot of a run, written to ``manifest.json``."""

    model_config = ConfigDict(use_enum_values=False)

    run_id: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    mode: str = Field(..., description="fixture|llm|gempy — how this run was produced.")
    request: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(default_factory=list)
    validation: ValidationReportPayload | None = None
    layer_summary: list[dict[str, Any]] = Field(default_factory=list)
    extent: dict[str, float] | None = None
    chunk_summary: dict[str, Any] | None = Field(
        default=None,
        description="Summary of ranked_chunks.json when a document was provided.",
    )
    llm_summary: dict[str, Any] | None = Field(
        default=None,
        description="Summary of the structured LLM extraction (Phase 5) when use_llm=true.",
    )
    dsl_summary: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Summary of the LLM-derived DSL (Phase 6) when the structured extraction "
            "could be deterministically converted into ROCK / DEPOSITION / INTRUSION "
            "statements."
        ),
    )
    gempy_inputs_summary: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Summary of gempy_inputs.json (Phase 7) — formation / surface point / "
            "orientation counts plus per-source provenance."
        ),
    )
    mesh_summary: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Summary of the mesh runner that produced the .glb (Phase 8) — "
            "engine name, fallback chain, vertex/face counts."
        ),
    )
    field_summary: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Summary of the depth-to-bedrock scalar field (Phase 9) — "
            "field engine, fallback chain, resolution, value stats."
        ),
    )
    error: str | None = None
    duration_ms: int | None = None

    def artifact_by_kind(self, kind: str) -> Artifact | None:
        for art in self.artifacts:
            if art.kind == kind:
                return art
        return None


class RunState(BaseModel):
    """Lightweight in-memory record (super-set of what we serve)."""

    model_config = ConfigDict(use_enum_values=False)

    run_id: str
    status: RunStatus
    manifest: RunManifest


__all__ = [
    "Artifact",
    "RunManifest",
    "RunState",
    "RunStatus",
    "ValidationIssue",
    "ValidationReportPayload",
]
