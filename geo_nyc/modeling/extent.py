"""Spatial extent / resolution dataclasses used across the modeling layer.

Kept in their own module (rather than co-located with mesh code) so the
fixture loader, run service, and tests can all use them without
pulling trimesh / scipy into their import graph.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelExtent:
    """Axis-aligned 3D bounding box in projected XY coordinates."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float  # deepest, most negative
    z_max: float  # ground surface, typically 0

    def __post_init__(self) -> None:
        if self.x_min >= self.x_max or self.y_min >= self.y_max or self.z_min >= self.z_max:
            raise ValueError(f"Invalid extent: {self}")

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def depth(self) -> float:
        return self.z_max - self.z_min

    @property
    def center_xy(self) -> tuple[float, float]:
        return ((self.x_min + self.x_max) / 2.0, (self.y_min + self.y_max) / 2.0)


@dataclass(frozen=True, slots=True)
class GridResolution:
    """Per-axis grid resolution for synthesised surfaces and scalar fields."""

    nx: int = 64
    ny: int = 64

    def __post_init__(self) -> None:
        if self.nx < 2 or self.ny < 2:
            raise ValueError(f"Grid resolution must be >= 2 in each axis, got {self}")
