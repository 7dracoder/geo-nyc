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

import math
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import RBFInterpolator

from geo_nyc.exceptions import ModelingError
from geo_nyc.modeling.constraints import GemPyInputs, SurfacePoint
from geo_nyc.modeling.runner import EngineName, MeshRunResult, _Timer
from geo_nyc.modeling.synthetic_mesh import LayerMesh

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

        layers: list[LayerMesh] = []
        per_layer_metadata: list[dict[str, object]] = []
        # Iterate in stratigraphic order so the returned list is oldest-
        # first — same contract as ``build_synthetic_layers``.
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
            vertices, faces = _grid_to_mesh(xv, yv, z_grid)
            color = formation.color_hex or _ROCK_TYPE_DEFAULT_COLOR.get(
                formation.rock_type, "#808080"
            )
            layers.append(
                LayerMesh(
                    surface_id=f"S_{formation.rock_id}",
                    name=formation.name,
                    rock_type=formation.rock_type,
                    color_hex=color,
                    vertices=vertices,
                    faces=faces,
                )
            )
            per_layer_metadata.append(
                {
                    "rock_id": formation.rock_id,
                    "anchor_count": len(anchors),
                    "interpolation": mode,
                    "z_min": float(z_grid.min()),
                    "z_max": float(z_grid.max()),
                }
            )

        # Enforce stratigraphic monotonicity: ensure no younger surface
        # dips below the older one underneath. This is the same gentle
        # post-processing GemPy applies via stack ordering — without it
        # the RBF can sometimes wiggle a metre into the layer below.
        layers, monotonicity_fixes = _enforce_stack_order(layers, extent.z_min)

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


def _enforce_stack_order(
    layers: list[LayerMesh], z_floor: float
) -> tuple[list[LayerMesh], int]:
    """Prevent younger surfaces from dipping below older ones.

    Returns ``(layers, fix_count)`` where ``fix_count`` reports how many
    grid cells we nudged upward. We never *lower* a surface — only raise
    a younger-than-floor surface to the floor of the older one beneath
    it, plus a tiny epsilon to keep GemPy / Three.js happy with strict
    inequality.
    """

    if len(layers) < 2:
        return layers, 0

    fixes = 0
    fixed: list[LayerMesh] = [layers[0]]
    eps = 0.05  # 5 cm clearance is plenty for visual + downstream interpolation
    for i in range(1, len(layers)):
        # Compare against the *already-fixed* layer below, not the
        # original, so corrections cascade upward through the stack.
        older = fixed[i - 1]
        younger = layers[i]
        floor_z = older.vertices[:, 2]
        younger_z = younger.vertices[:, 2].copy()
        violations = younger_z < (floor_z + eps)
        if violations.any():
            fixes += int(violations.sum())
            younger_z[violations] = floor_z[violations] + eps
            new_vertices = younger.vertices.copy()
            new_vertices[:, 2] = np.clip(younger_z, z_floor, math.inf)
            fixed.append(
                LayerMesh(
                    surface_id=younger.surface_id,
                    name=younger.name,
                    rock_type=younger.rock_type,
                    color_hex=younger.color_hex,
                    vertices=new_vertices,
                    faces=younger.faces,
                )
            )
        else:
            fixed.append(younger)
    return fixed, fixes


def _grid_to_mesh(
    xv: np.ndarray, yv: np.ndarray, zv: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Triangulate a structured ``(x, y, z)`` grid (mirrors synthetic_mesh)."""

    ny, nx = xv.shape
    vertices = np.column_stack(
        [xv.ravel(), yv.ravel(), zv.ravel()]
    ).astype(np.float64, copy=False)

    faces = np.empty(((ny - 1) * (nx - 1) * 2, 3), dtype=np.int32)
    fi = 0
    for j in range(ny - 1):
        for i in range(nx - 1):
            v00 = j * nx + i
            v10 = j * nx + (i + 1)
            v01 = (j + 1) * nx + i
            v11 = (j + 1) * nx + (i + 1)
            faces[fi] = (v00, v10, v11)
            faces[fi + 1] = (v00, v11, v01)
            fi += 2
    return vertices, faces


__all__ = ["RBFRunner", "RBFRunnerConfig"]
