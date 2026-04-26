"""Pydantic schemas for the GemPy input layer.

These structures sit *between* the validated DSL ``Program`` and GemPy's
``data`` API. We keep them grounded in plain Pydantic (rather than
importing GemPy types here) for three reasons:

1. The constraint builder runs in test environments where GemPy isn't
   installed (e.g. the CI image and the docs builder).
2. We persist them as ``data/runs/{run_id}/gempy_inputs.json`` so the
   frontend and Part 3 can inspect provenance even before a GemPy run
   succeeds.
3. The same schema feeds the eventual GemPy runner (Phase 8) and any
   alternative back-end (e.g. PyVista, scikit-fmm) without coupling.

Every constraint records a :class:`ConstraintSource` so the API surface
can honestly say which numbers came from the LLM, which were inferred
from glossary defaults, and which are pure fixture stand-ins.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ConstraintSource = Literal["extracted", "inferred", "fixture"]
RockTypeName = Literal["sedimentary", "volcanic", "intrusive", "metamorphic"]


class FormationConstraint(BaseModel):
    """One stratigraphic unit, ready to be loaded into GemPy."""

    model_config = ConfigDict(frozen=True)

    rock_id: str = Field(..., min_length=1, description="DSL identifier (e.g. R_MANHATTAN_SCHIST).")
    name: str = Field(..., min_length=1, description="Canonical formation name.")
    rock_type: RockTypeName
    color_hex: str | None = None
    stratigraphic_order: int = Field(
        ...,
        ge=0,
        description="0 = oldest, increasing = younger. Drives GemPy stack order.",
    )
    is_intrusive: bool = False
    age_ma: float | None = Field(default=None, ge=0.0, le=5_000.0)
    source: ConstraintSource = "fixture"
    note: str | None = None


class SurfacePoint(BaseModel):
    """An ``(x, y, z)`` anchor where a formation's *top* surface passes through."""

    model_config = ConfigDict(frozen=True)

    formation_id: str = Field(..., min_length=1)
    x: float
    y: float
    z: float = Field(..., description="Elevation; negative values are below ground.")
    source: ConstraintSource = "fixture"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_quote: str | None = Field(default=None, max_length=600)
    note: str | None = None


class Orientation(BaseModel):
    """A dip / strike measurement attached to a formation."""

    model_config = ConfigDict(frozen=True)

    formation_id: str = Field(..., min_length=1)
    x: float
    y: float
    z: float
    dip_degrees: float = Field(..., ge=0.0, le=90.0)
    azimuth_degrees: float = Field(..., ge=0.0, lt=360.0)
    polarity: int = Field(default=1, ge=-1, le=1)
    source: ConstraintSource = "fixture"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_quote: str | None = Field(default=None, max_length=600)
    note: str | None = None

    @field_validator("polarity")
    @classmethod
    def _polarity_nonzero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("polarity must be -1 or 1, never 0")
        return value


class ExtentBox(BaseModel):
    """Axis-aligned 3D extent expressed in projected metres."""

    model_config = ConfigDict(frozen=True)

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    @field_validator("x_max")
    @classmethod
    def _x_axis(cls, v: float, info: Any) -> float:
        x_min = info.data.get("x_min")
        if x_min is not None and v <= x_min:
            raise ValueError(f"x_max ({v}) must be > x_min ({x_min}).")
        return v

    @field_validator("y_max")
    @classmethod
    def _y_axis(cls, v: float, info: Any) -> float:
        y_min = info.data.get("y_min")
        if y_min is not None and v <= y_min:
            raise ValueError(f"y_max ({v}) must be > y_min ({y_min}).")
        return v

    @field_validator("z_max")
    @classmethod
    def _z_axis(cls, v: float, info: Any) -> float:
        z_min = info.data.get("z_min")
        if z_min is not None and v <= z_min:
            raise ValueError(f"z_max ({v}) must be > z_min ({z_min}).")
        return v


class GridResolution3D(BaseModel):
    """Per-axis grid resolution for the eventual GemPy regular grid."""

    model_config = ConfigDict(frozen=True)

    nx: int = Field(default=32, ge=2, le=512)
    ny: int = Field(default=32, ge=2, le=512)
    nz: int = Field(default=32, ge=2, le=512)


class GemPyInputs(BaseModel):
    """Everything Phase 8 needs to spin up a GemPy model deterministically."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    extent: ExtentBox
    crs: str = Field(..., min_length=1, description="EPSG/WKT identifier (e.g. 'EPSG:32618').")
    grid_resolution: GridResolution3D = Field(default_factory=GridResolution3D)
    formations: list[FormationConstraint] = Field(default_factory=list)
    surface_points: list[SurfacePoint] = Field(default_factory=list)
    orientations: list[Orientation] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_demo_ready(self) -> bool:
        """At least 2 formations, 2 surface points, and 1 orientation."""

        return (
            len(self.formations) >= 2
            and len(self.surface_points) >= 2
            and len(self.orientations) >= 1
        )


__all__ = [
    "ConstraintSource",
    "ExtentBox",
    "FormationConstraint",
    "GemPyInputs",
    "GridResolution3D",
    "Orientation",
    "RockTypeName",
    "SurfacePoint",
]
