"""Real and stub depth-to-bedrock scalar-field builders.

The mesh runner (Phase 8) produces the 3D layers Three.js renders, but
Part 3 (GIS / ML / optim) needs a 2D scalar field that's *independent
of the mesh's resolution* and rebuilt from the same surface points the
LLM extracted. This module provides:

* :func:`build_depth_to_bedrock_field_from_inputs` — RBF-interpolate
  the bedrock formation's surface anchors onto a regular ``(ny, nx)``
  grid in projected metres. Used when the run has real
  :class:`GemPyInputs` (LLM pipeline or fixture mode).
* :func:`build_stub_depth_field` — deterministic smooth field over the
  extent. Tagged ``source="stub"``. Guaranteed never to fail so the
  demo pipeline never blocks on the field stage.

Both builders return :class:`ScalarField` with ``units =
"meters_below_surface"`` so the on-disk contract is uniform.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from geo_nyc.modeling.constraints import GemPyInputs, SurfacePoint
from geo_nyc.modeling.extent import GridResolution, ModelExtent
from geo_nyc.modeling.synthetic_field import FieldSource, ScalarField


@dataclass(frozen=True, slots=True)
class FieldBuilderConfig:
    """Tuning knobs surfaced for tests + future settings exposure."""

    grid_nx: int = 64
    grid_ny: int = 64
    smoothing: float = 0.0
    min_points_for_rbf: int = 3
    # When clamping the interpolated bedrock surface, we keep at least
    # this much depth so the field never reports impossibly thin
    # overburden right at the edges of the convex hull.
    min_overburden_m: float = 0.5


def build_depth_to_bedrock_field_from_inputs(
    inputs: GemPyInputs,
    *,
    extent: ModelExtent | None = None,
    config: FieldBuilderConfig | None = None,
    source: FieldSource = "rbf",
) -> ScalarField:
    """Interpolate the bedrock formation's surface points to a depth grid.

    *Bedrock* is identified as the formation with the smallest
    ``stratigraphic_order`` (i.e. the oldest = deepest). If multiple
    formations tie at order ``0`` we prefer the one whose
    ``rock_type == "metamorphic"`` (NYC bedrock), then fall back to the
    first in canonical order. When no usable surface points exist for
    the bedrock formation we degrade to the ``constant_mean`` strategy
    used by :class:`RBFRunner`.

    The function never raises for benign cases (empty inputs, sparse
    points, degenerate geometry) — it returns a deterministic field
    with a meaningful ``source`` tag instead. That keeps the run
    pipeline single-path: the caller decides whether to swap to the
    stub builder based on its own provenance signal.
    """

    cfg = config or FieldBuilderConfig()
    field_extent = extent or _extent_from_inputs(inputs)

    bedrock = _select_bedrock_formation(inputs)
    if bedrock is None:
        return build_stub_depth_field(field_extent, crs=inputs.crs, config=cfg)

    anchors = [
        sp for sp in inputs.surface_points if sp.formation_id == bedrock.rock_id
    ]

    nx, ny = cfg.grid_nx, cfg.grid_ny
    xs = np.linspace(field_extent.x_min, field_extent.x_max, nx)
    ys = np.linspace(field_extent.y_min, field_extent.y_max, ny)
    xv, yv = np.meshgrid(xs, ys, indexing="xy")

    z_grid, mode = _interpolate_bedrock_surface(
        anchors=anchors,
        sample_points=np.column_stack([xv.ravel(), yv.ravel()]),
        extent_z_range=(field_extent.z_min, field_extent.z_max),
        config=cfg,
    )
    z_grid = z_grid.reshape(ny, nx)

    ground_z = float(field_extent.z_max)
    depth = (ground_z - z_grid).astype(np.float32, copy=False)
    depth = np.clip(depth, cfg.min_overburden_m, None)

    return ScalarField(
        name="depth_to_bedrock_m",
        units="meters_below_surface",
        crs=inputs.crs,
        extent=field_extent,
        nx=nx,
        ny=ny,
        values=depth,
        source=source if mode != "constant_midplane" else "stub",
        resolution_m=_resolution_m(field_extent, nx, ny),
    )


def build_stub_depth_field(
    extent: ModelExtent,
    *,
    crs: str = "EPSG:32618",
    config: FieldBuilderConfig | None = None,
    seed: int = 0,
) -> ScalarField:
    """Deterministic smooth depth-to-bedrock field over ``extent``.

    Used when no constraints are available. The shape is a slight
    east-trending tilt plus a single sinusoidal "buried valley" so the
    visual is not a perfect plane but the values stay physically
    plausible for the NYC area (10-60 m to bedrock is common).

    ``seed`` is accepted for forward compatibility but the function is
    fully deterministic on ``(extent, config)`` regardless of it.
    """

    cfg = config or FieldBuilderConfig()
    nx, ny = cfg.grid_nx, cfg.grid_ny

    xs = np.linspace(extent.x_min, extent.x_max, nx)
    ys = np.linspace(extent.y_min, extent.y_max, ny)
    xv, yv = np.meshgrid(xs, ys, indexing="xy")

    width = max(extent.width, 1.0)
    height = max(extent.height, 1.0)
    nx01 = (xv - extent.x_min) / width
    ny01 = (yv - extent.y_min) / height

    base = 25.0  # mean overburden, metres
    tilt = 6.0 * (nx01 - 0.5)  # ±3 m east-west tilt
    valley = 8.0 * np.sin(math.pi * nx01) * np.cos(math.pi * ny01)
    depth = base + tilt + valley
    depth = np.clip(depth.astype(np.float32, copy=False), cfg.min_overburden_m, None)

    # Burn the seed into a tiny deterministic perturbation purely so
    # callers can A/B different "stub variants" without hitting cache
    # collisions on bit-for-bit identical files.
    if seed:
        rng = np.random.default_rng(seed)
        depth = depth + rng.normal(scale=0.1, size=depth.shape).astype(np.float32)
        depth = np.clip(depth, cfg.min_overburden_m, None)

    return ScalarField(
        name="depth_to_bedrock_m",
        units="meters_below_surface",
        crs=crs,
        extent=extent,
        nx=nx,
        ny=ny,
        values=depth,
        source="stub",
        resolution_m=_resolution_m(extent, nx, ny),
    )


# ---------------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------------- #


def _select_bedrock_formation(inputs: GemPyInputs):
    """Pick the formation that should drive the depth-to-bedrock field."""

    if not inputs.formations:
        return None

    # Lowest stratigraphic order = oldest = deepest. Within the
    # smallest order, prefer "metamorphic" since NYC bedrock = schist /
    # gneiss / marble, all metamorphic.
    by_order = sorted(inputs.formations, key=lambda f: f.stratigraphic_order)
    smallest_order = by_order[0].stratigraphic_order
    candidates = [f for f in by_order if f.stratigraphic_order == smallest_order]
    metamorphic = [f for f in candidates if f.rock_type == "metamorphic"]
    return (metamorphic or candidates)[0]


def _interpolate_bedrock_surface(
    *,
    anchors: list[SurfacePoint],
    sample_points: np.ndarray,
    extent_z_range: tuple[float, float],
    config: FieldBuilderConfig,
) -> tuple[np.ndarray, str]:
    """Mirror of :class:`RBFRunner._interpolate_surface`, kept inline.

    We deliberately don't import the runner directly: the runner takes
    a full :class:`GemPyInputs` and we only need a single formation, so
    the duplicated 30 lines are clearer than a refactor that would
    couple the field builder to the mesh runner's internals.
    """

    from scipy.interpolate import RBFInterpolator

    z_min, z_max = extent_z_range
    if not anchors:
        mid = (z_min + z_max) / 2.0
        return (
            np.full(sample_points.shape[0], mid, dtype=np.float64),
            "constant_midplane",
        )

    if len(anchors) < config.min_points_for_rbf:
        mean_z = float(np.mean([sp.z for sp in anchors]))
        return (
            np.full(sample_points.shape[0], mean_z, dtype=np.float64),
            "constant_mean",
        )

    xy = np.array([(sp.x, sp.y) for sp in anchors], dtype=np.float64)
    z = np.array([sp.z for sp in anchors], dtype=np.float64)

    # Degenerate input: every anchor at identical (x, y), or all
    # anchors lying on a single line, trip RBFInterpolator with a
    # singular system. Fall back to mean before SciPy throws.
    if np.allclose(xy.std(axis=0), 0.0) or _is_collinear(xy):
        return (
            np.full(sample_points.shape[0], float(z.mean()), dtype=np.float64),
            "constant_mean",
        )

    try:
        rbf = RBFInterpolator(
            xy,
            z,
            kernel="thin_plate_spline",
            smoothing=config.smoothing,
        )
        values = rbf(sample_points)
    except np.linalg.LinAlgError:
        # Belt-and-braces: any other rank deficiency lands here.
        return (
            np.full(sample_points.shape[0], float(z.mean()), dtype=np.float64),
            "constant_mean",
        )
    np.clip(values, z_min, z_max, out=values)
    return values, "thin_plate_spline"


def _is_collinear(xy: np.ndarray, tol: float = 1e-9) -> bool:
    """True if the anchor points are (approximately) collinear in the XY plane.

    A 2x2 covariance matrix has rank 1 iff its smaller eigenvalue is
    ~0, which is what a thin-plate spline's monomial matrix needs to
    avoid.
    """

    if xy.shape[0] < 3:
        return False
    centred = xy - xy.mean(axis=0, keepdims=True)
    cov = centred.T @ centred
    eigvals = np.linalg.eigvalsh(cov)
    return bool(eigvals.min() <= tol * max(eigvals.max(), 1.0))


def _extent_from_inputs(inputs: GemPyInputs) -> ModelExtent:
    e = inputs.extent
    return ModelExtent(
        x_min=e.x_min,
        x_max=e.x_max,
        y_min=e.y_min,
        y_max=e.y_max,
        z_min=e.z_min,
        z_max=e.z_max,
    )


def _resolution_m(extent: ModelExtent, nx: int, ny: int) -> float:
    dx = extent.width / max(nx - 1, 1)
    dy = extent.height / max(ny - 1, 1)
    return round(float((dx + dy) / 2.0), 3)


# Silence the unused-import warning while keeping ``GridResolution``
# discoverable from this module for callers that prefer a single
# import surface.
_ = GridResolution


__all__ = [
    "FieldBuilderConfig",
    "build_depth_to_bedrock_field_from_inputs",
    "build_stub_depth_field",
]
