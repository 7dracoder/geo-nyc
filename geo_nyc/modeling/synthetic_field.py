"""Depth-to-bedrock scalar field on a regular grid.

The ``ScalarField`` here is the on-disk contract Part 3 (GIS / ML /
optim) consumes. It carries enough provenance to tell whether the
numbers came from GemPy, the in-process RBF runner, or the deterministic
stub fallback.

Two builders live in this module:

* :func:`build_depth_to_bedrock_field` — derive the field by resampling
  the *deepest* :class:`LayerMesh` produced by the mesh runner. This is
  the cheapest path because the mesh is already cached on disk.
* The real RBF / GemPy field builders sit in
  :mod:`geo_nyc.modeling.field_builder` so they can interpolate
  directly from :class:`GemPyInputs` (the LLM's own surface points)
  even when no mesh runner was invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from geo_nyc.modeling.extent import GridResolution, ModelExtent
from geo_nyc.modeling.synthetic_mesh import LayerMesh

FieldSource = Literal["gempy", "rbf", "synthetic", "stub"]


@dataclass(frozen=True, slots=True)
class ScalarField:
    """Regular-grid 2D scalar field plus its world-space metadata.

    ``values`` is shaped ``(ny, nx)`` and is laid out so that
    ``values[j, i]`` corresponds to ``(x_coords[i], y_coords[j])`` —
    matching :func:`numpy.meshgrid` with ``indexing="xy"``.

    ``mask`` (optional) is a ``(ny, nx)`` ``uint8`` array where ``1``
    marks valid cells and ``0`` marks cells the upstream interpolator
    couldn't fill (e.g. NaNs outside the convex hull of the anchors).
    Consumers should ignore masked-out cells.

    ``source`` records which engine produced the field; the disk
    sidecar mirrors this so Part 3 can branch on quality.
    """

    name: str
    units: str
    crs: str
    extent: ModelExtent
    nx: int
    ny: int
    values: np.ndarray  # shape (ny, nx), float32
    source: FieldSource = "synthetic"
    resolution_m: float | None = None
    mask: np.ndarray | None = None  # shape (ny, nx), uint8

    def __post_init__(self) -> None:
        if self.values.shape != (self.ny, self.nx):
            raise ValueError(
                f"ScalarField shape mismatch: got {self.values.shape}, expected ({self.ny}, {self.nx})"
            )
        if self.mask is not None:
            if self.mask.shape != (self.ny, self.nx):
                raise ValueError(
                    f"ScalarField mask shape mismatch: got {self.mask.shape}, expected ({self.ny}, {self.nx})"
                )
            if self.mask.dtype != np.uint8:
                raise ValueError(
                    f"ScalarField mask must be uint8, got {self.mask.dtype}"
                )

    @property
    def x_coords(self) -> np.ndarray:
        return np.linspace(self.extent.x_min, self.extent.x_max, self.nx)

    @property
    def y_coords(self) -> np.ndarray:
        return np.linspace(self.extent.y_min, self.extent.y_max, self.ny)

    @property
    def has_mask(self) -> bool:
        return self.mask is not None

    def stats(self) -> dict[str, float]:
        """Min / max / mean of the *valid* (mask==1) cells."""

        if self.mask is None:
            v = self.values
        else:
            v = self.values[self.mask.astype(bool)]
        if v.size == 0:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        return {
            "min": float(np.min(v)),
            "max": float(np.max(v)),
            "mean": float(np.mean(v)),
        }


def build_depth_to_bedrock_field(
    layers: list[LayerMesh],
    extent: ModelExtent,
    *,
    crs: str = "EPSG:32618",
    resolution: GridResolution | None = None,
    source: FieldSource = "synthetic",
) -> ScalarField:
    """Compute *depth-to-bedrock* (positive metres) by resampling ``layers[0]``.

    The bedrock surface is taken as the deepest layer in ``layers`` —
    same convention as :func:`build_synthetic_layers`, where the
    returned list is oldest-first. We sample its z-value on a regular
    ``(ny, nx)`` grid and report ``ground_z - bedrock_z``.

    This is intentionally agnostic about whether the layers came from
    the synthetic / RBF / GemPy runners; the caller passes ``source``
    so the produced field can be tagged honestly.
    """

    if not layers:
        raise ValueError("Need at least one layer to derive a depth field.")

    res = resolution or GridResolution()
    bedrock = layers[0]
    bedrock_z = _resample_layer_to_grid(bedrock, extent, res)
    ground_z = extent.z_max

    depth = np.asarray(ground_z - bedrock_z, dtype=np.float32)
    depth = np.clip(depth, 0.0, None)

    resolution_m = _resolution_m_from_extent(extent, res)
    return ScalarField(
        name="depth_to_bedrock_m",
        units="meters_below_surface",
        crs=crs,
        extent=extent,
        nx=res.nx,
        ny=res.ny,
        values=depth,
        source=source,
        resolution_m=resolution_m,
    )


def _resample_layer_to_grid(
    layer: LayerMesh, extent: ModelExtent, res: GridResolution
) -> np.ndarray:
    """Resample a structured-grid layer back onto a regular ``(ny, nx)`` array.

    Handles two layouts:
    * **Legacy top-only surface** (``nx * ny`` vertices): reshape directly.
    * **Closed slab** (``2 * nx * ny`` vertices, top half then bottom):
      the *top* of the slab is the geological surface we care about for
      depth-to-bedrock, so we slice the first half before reshaping.
    """

    expected = res.nx * res.ny
    n_verts = layer.vertices.shape[0]
    if n_verts == expected:
        return layer.vertices[:, 2].reshape(res.ny, res.nx)
    if n_verts == 2 * expected:
        return layer.vertices[:expected, 2].reshape(res.ny, res.nx)
    return _nearest_resample(layer, extent, res)


def _nearest_resample(
    layer: LayerMesh, extent: ModelExtent, res: GridResolution
) -> np.ndarray:
    from scipy.spatial import cKDTree  # local import keeps scipy off the hot path

    xs = np.linspace(extent.x_min, extent.x_max, res.nx)
    ys = np.linspace(extent.y_min, extent.y_max, res.ny)
    xv, yv = np.meshgrid(xs, ys, indexing="xy")
    grid_xy = np.column_stack([xv.ravel(), yv.ravel()])

    tree = cKDTree(layer.vertices[:, :2])
    _, idx = tree.query(grid_xy, k=1)
    return layer.vertices[idx, 2].reshape(res.ny, res.nx)


def _resolution_m_from_extent(extent: ModelExtent, res: GridResolution) -> float:
    """Average sample spacing in metres (assumes a projected CRS)."""

    dx = extent.width / max(res.nx - 1, 1)
    dy = extent.height / max(res.ny - 1, 1)
    return round(float((dx + dy) / 2.0), 3)


__all__ = [
    "FieldSource",
    "ScalarField",
    "build_depth_to_bedrock_field",
]
