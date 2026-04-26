"""Mesh runner that interpolates surface points with a thin-plate RBF.

This is the default "real" runner: it consumes :class:`GemPyInputs`
(per-formation surface points + orientations) and fits a smooth
top-of-formation surface for each layer using
:class:`scipy.interpolate.RBFInterpolator` with a thin-plate spline
kernel. The result has the same flavour as GemPy's surface
interpolator (the GemPy team also uses RBFs internally) but runs in
~50 ms on the fixture extent and avoids the heavy GemPy install.

Each interpolated surface is sampled on a regular ``(nx, ny)`` grid,
clamped to the model extent's ``z`` range, and triangulated into a
:class:`LayerMesh` matching the synthetic runner's interface so the
existing glTF exporter is reused unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import RBFInterpolator

from geo_nyc.exceptions import ModelingError
from geo_nyc.modeling.constraints import GemPyInputs, SurfacePoint
from geo_nyc.modeling.runner import EngineName, MeshRunResult, _Timer
from geo_nyc.modeling.synthetic_mesh import LayerMesh, grid_slab_to_mesh

# Default colours used when neither the glossary nor the constraint
# carries one. Mirrors ``synthetic_mesh._ROCK_TYPE_DEFAULT_COLOR`` so
# both runners feel visually consistent in the demo.
_ROCK_TYPE_DEFAULT_COLOR: dict[str, str] = {
    "sedimentary": "#D2B48C",
    "volcanic": "#2F4F4F",
    "intrusive": "#808080",
    "metamorphic": "#5C4A3A",
}


@dataclass(frozen=True, slots=True)
class RBFRunnerConfig:
    """Tuning knobs surfaced for tests + future settings exposure."""

    grid_nx: int = 64
    grid_ny: int = 64
    smoothing: float = 0.0
    # Falls back to a single global plane if a formation has < 3 anchors;
    # at 3+ points a thin-plate spline is well-defined.
    min_points_for_rbf: int = 3


@dataclass(slots=True)
class _FormationSurface:
    """Per-formation top surface bookkeeping during the slab build."""

    rock_id: str
    name: str
    rock_type: str
    color_hex: str
    top_z: np.ndarray  # (ny, nx) float64 — top of formation
    anchor_count: int
    interpolation: str


class RBFRunner:
    """Mesh runner backed by :class:`scipy.interpolate.RBFInterpolator`."""

    name: EngineName = "rbf"

    def __init__(self, config: RBFRunnerConfig | None = None) -> None:
        self._config = config or RBFRunnerConfig()

    def is_available(self) -> bool:
        # scipy is a hard dep; this runner is always available.
        return True

    def run(self, inputs: GemPyInputs) -> MeshRunResult:
        timer = _Timer.start()
        if not inputs.formations:
            return MeshRunResult(
                engine=self.name,
                layers=[],
                duration_ms=timer.elapsed_ms,
                metadata={"reason": "no_formations"},
            )

        extent = inputs.extent
        nx, ny = self._config.grid_nx, self._config.grid_ny
        xs = np.linspace(extent.x_min, extent.x_max, nx)
        ys = np.linspace(extent.y_min, extent.y_max, ny)
        xv, yv = np.meshgrid(xs, ys, indexing="xy")
        sample_points = np.column_stack([xv.ravel(), yv.ravel()])

        points_by_formation: dict[str, list[SurfacePoint]] = {}
        for sp in inputs.surface_points:
            points_by_formation.setdefault(sp.formation_id, []).append(sp)

        # Step 1: interpolate the *top* surface of every formation in
        # stratigraphic order (oldest-first) so we can later stack each
        # formation on top of the older one beneath it.
        top_surfaces: list[_FormationSurface] = []
        formations_sorted = sorted(
            inputs.formations, key=lambda f: f.stratigraphic_order
        )
        for formation in formations_sorted:
            anchors = points_by_formation.get(formation.rock_id, [])
            try:
                z_grid, mode = self._interpolate_surface(
                    anchors=anchors,
                    sample_points=sample_points,
                    extent_z_range=(extent.z_min, extent.z_max),
                )
            except ModelingError:
                raise
            except Exception as exc:  # pragma: no cover - SciPy edge cases
                raise ModelingError(
                    f"RBF interpolation failed for {formation.name}: {exc}"
                ) from exc

            z_grid = z_grid.reshape(ny, nx)
            color = formation.color_hex or _ROCK_TYPE_DEFAULT_COLOR.get(
                formation.rock_type, "#808080"
            )
            top_surfaces.append(
                _FormationSurface(
                    rock_id=formation.rock_id,
                    name=formation.name,
                    rock_type=formation.rock_type,
                    color_hex=color,
                    top_z=z_grid,
                    anchor_count=len(anchors),
                    interpolation=mode,
                )
            )

        # Step 2a: clamp every top_z into its assigned stratigraphic
        # band. Without this, an under-constrained RBF can throw the
        # deepest formation's top surface up to z_max in places, so
        # the upper layers all collapse onto the same plane and only
        # the deepest slab is visible. We re-centre each surface on
        # an evenly-spaced target depth and cap the relief at a
        # fraction of the layer thickness.
        top_surfaces = _clamp_to_strat_bands(top_surfaces, extent.z_min, extent.z_max)

        # Step 2b: enforce stratigraphic monotonicity on the *top*
        # surfaces alone — younger top can't dip below the older one
        # beneath it (same post-process GemPy applies via stack order).
        top_surfaces, monotonicity_fixes = _enforce_top_z_stack(
            top_surfaces, extent.z_min
        )

        # Step 3: turn each top surface into a closed *slab* whose
        # bottom is the previous (older) formation's top — or the
        # extent floor for the deepest one. This is what makes each
        # formation render as a real volumetric block instead of a
        # paper-thin sheet.
        layers: list[LayerMesh] = []
        per_layer_metadata: list[dict[str, object]] = []
        floor_grid = np.full_like(top_surfaces[0].top_z, extent.z_min) if top_surfaces else None
        prev_top: np.ndarray | None = None
        for surface in top_surfaces:
            bottom_z = prev_top if prev_top is not None else floor_grid
            assert bottom_z is not None  # for type-checkers; guarded by `if top_surfaces`
            vertices, faces = grid_slab_to_mesh(xv, yv, surface.top_z, bottom_z)
            layers.append(
                LayerMesh(
                    surface_id=f"S_{surface.rock_id}",
                    name=surface.name,
                    rock_type=surface.rock_type,
                    color_hex=surface.color_hex,
                    vertices=vertices,
                    faces=faces,
                )
            )
            per_layer_metadata.append(
                {
                    "rock_id": surface.rock_id,
                    "anchor_count": surface.anchor_count,
                    "interpolation": surface.interpolation,
                    "z_min": float(bottom_z.min()),
                    "z_max": float(surface.top_z.max()),
                }
            )
            prev_top = surface.top_z

        return MeshRunResult(
            engine=self.name,
            layers=layers,
            duration_ms=timer.elapsed_ms,
            metadata={
                "grid_nx": nx,
                "grid_ny": ny,
                "monotonicity_fixes": monotonicity_fixes,
                "layers": per_layer_metadata,
            },
        )

    def _interpolate_surface(
        self,
        *,
        anchors: list[SurfacePoint],
        sample_points: np.ndarray,
        extent_z_range: tuple[float, float],
    ) -> tuple[np.ndarray, str]:
        """Return ``(z_at_grid, mode)`` for one formation."""

        z_min, z_max = extent_z_range
        if not anchors:
            mid = (z_min + z_max) / 2.0
            return np.full(sample_points.shape[0], mid, dtype=np.float64), "constant_midplane"

        if len(anchors) < self._config.min_points_for_rbf:
            mean_z = float(np.mean([sp.z for sp in anchors]))
            return np.full(sample_points.shape[0], mean_z, dtype=np.float64), "constant_mean"

        xy = np.array([(sp.x, sp.y) for sp in anchors], dtype=np.float64)
        z = np.array([sp.z for sp in anchors], dtype=np.float64)

        # Detect degenerate input: every anchor at identical (x, y),
        # or all anchors lying on a single line, trip RBFInterpolator
        # with a singular monomial matrix. Fall back to mean before
        # SciPy throws.
        if np.allclose(xy.std(axis=0), 0.0) or _is_collinear(xy):
            return np.full(sample_points.shape[0], float(z.mean()), dtype=np.float64), "constant_mean"

        try:
            rbf = RBFInterpolator(
                xy,
                z,
                kernel="thin_plate_spline",
                smoothing=self._config.smoothing,
            )
            values = rbf(sample_points)
        except np.linalg.LinAlgError:
            return np.full(sample_points.shape[0], float(z.mean()), dtype=np.float64), "constant_mean"
        np.clip(values, z_min, z_max, out=values)
        return values, "thin_plate_spline"


def _is_collinear(xy: np.ndarray, tol: float = 1e-9) -> bool:
    """True if anchors are (approximately) collinear in the XY plane."""

    if xy.shape[0] < 3:
        return False
    centred = xy - xy.mean(axis=0, keepdims=True)
    cov = centred.T @ centred
    eigvals = np.linalg.eigvalsh(cov)
    return bool(eigvals.min() <= tol * max(eigvals.max(), 1.0))


def _clamp_to_strat_bands(
    surfaces: list[_FormationSurface],
    z_min: float,
    z_max: float,
    *,
    relief_fraction: float = 1.0,
) -> list[_FormationSurface]:
    """Bound each top surface's relief around its own natural mean.

    Even with anchor points the thin-plate-spline can extrapolate to
    ``z_max`` (or ``z_min``) far away from any anchor. Without this
    step the deepest formation can have peaks reaching the surface,
    visually swallowing every younger layer above it.

    We keep each surface's *anchor-derived mean* (so a deep bedrock
    keeps reading deep), but cap the relief at ``relief_fraction``
    times the per-layer thickness. ``_enforce_top_z_stack`` is run
    afterwards to resolve any remaining overlap between adjacent
    formations.
    """

    n = len(surfaces)
    if n == 0:
        return surfaces
    depth = max(z_max - z_min, 1e-6)
    layer_thickness = depth / n
    half_relief = layer_thickness * 0.5 * relief_fraction

    out: list[_FormationSurface] = []
    for surface in surfaces:
        natural_mean = float(np.mean(surface.top_z))
        deviation = surface.top_z - natural_mean
        clipped = np.clip(deviation, -half_relief, half_relief)
        new_top = np.clip(natural_mean + clipped, z_min, z_max)
        out.append(
            _FormationSurface(
                rock_id=surface.rock_id,
                name=surface.name,
                rock_type=surface.rock_type,
                color_hex=surface.color_hex,
                top_z=new_top,
                anchor_count=surface.anchor_count,
                interpolation=surface.interpolation,
            )
        )
    return out


def _enforce_top_z_stack(
    surfaces: list[_FormationSurface], z_floor: float
) -> tuple[list[_FormationSurface], int]:
    """Prevent younger top surfaces from dipping below older ones.

    Returns ``(surfaces, fix_count)`` where ``fix_count`` reports how
    many grid cells we nudged upward. We never *lower* a surface — only
    raise a younger surface to the older one beneath it plus a tiny
    epsilon, so the resulting slabs always have non-negative thickness.
    """

    if len(surfaces) < 2:
        return surfaces, 0

    fixes = 0
    fixed: list[_FormationSurface] = [surfaces[0]]
    eps = 0.05  # 5 cm clearance keeps slabs strictly non-degenerate
    for i in range(1, len(surfaces)):
        # Compare against the *already-fixed* surface below, not the
        # original, so corrections cascade upward through the stack.
        older = fixed[i - 1]
        younger = surfaces[i]
        floor_z = older.top_z
        younger_z = younger.top_z.copy()
        violations = younger_z < (floor_z + eps)
        if violations.any():
            fixes += int(violations.sum())
            younger_z[violations] = floor_z[violations] + eps
            np.clip(younger_z, z_floor, np.inf, out=younger_z)
            fixed.append(
                _FormationSurface(
                    rock_id=younger.rock_id,
                    name=younger.name,
                    rock_type=younger.rock_type,
                    color_hex=younger.color_hex,
                    top_z=younger_z,
                    anchor_count=younger.anchor_count,
                    interpolation=younger.interpolation,
                )
            )
        else:
            fixed.append(younger)
    return fixed, fixes


__all__ = ["RBFRunner", "RBFRunnerConfig"]
