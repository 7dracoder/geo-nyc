"""Real GemPy runner (optional extra ``geo-nyc[modeling]``).

GemPy is heavy and version-sensitive on Apple Silicon, so it is *not*
a hard dependency. The runner lazy-imports ``gempy`` on first call and
raises :class:`GemPyUnavailableError` if the import fails. The public
contract matches :class:`MeshRunner` so the run service treats this
runner identically to the RBF and synthetic alternatives.

The implementation targets GemPy 2024.2.x. We:

1. Convert :class:`GemPyInputs` into the data tables GemPy expects
   (``surface_points`` and ``orientations`` DataFrames).
2. Build a ``GeoModel`` with the requested extent + grid resolution.
3. Run the implicit-function solver via ``gempy.compute_model``.
4. Pull per-formation surface meshes out of
   ``geo_model.solutions.raw_arrays.vertices`` /
   ``solutions.raw_arrays.edges`` and wrap them as :class:`LayerMesh`.

We never let GemPy import errors leak past this module — every failure
becomes a :class:`ModelingError` so the run service can fall back.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import numpy as np

from geo_nyc.exceptions import GemPyUnavailableError, ModelingError
from geo_nyc.modeling.constraints import GemPyInputs
from geo_nyc.modeling.runner import EngineName, MeshRunResult, _Timer
from geo_nyc.modeling.synthetic_mesh import LayerMesh

_ROCK_TYPE_DEFAULT_COLOR: dict[str, str] = {
    "sedimentary": "#D2B48C",
    "volcanic": "#2F4F4F",
    "intrusive": "#808080",
    "metamorphic": "#5C4A3A",
}


@dataclass(frozen=True, slots=True)
class GemPyRunnerConfig:
    """Knobs for the real GemPy invocation."""

    refinement: int = 4
    project_name: str = "geo-nyc"
    minimum_orientations_per_group: int = 2


class GemPyRunner:
    """Adapter that calls real GemPy when the ``modeling`` extra is installed."""

    name: EngineName = "gempy"

    def __init__(self, config: GemPyRunnerConfig | None = None) -> None:
        self._config = config or GemPyRunnerConfig()
        self._gempy_module: ModuleType | None = None

    # ------------------------------------------------------------------
    # Probes
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        try:
            self._import_gempy()
        except GemPyUnavailableError:
            return False
        return True

    def _import_gempy(self) -> ModuleType:
        if self._gempy_module is not None:
            return self._gempy_module
        try:
            module = importlib.import_module("gempy")
        except ImportError as exc:
            raise GemPyUnavailableError(
                "GemPy is not installed. Install the optional 'modeling' extra "
                "(pip install -e .[modeling]) to enable the real GemPy runner."
            ) from exc
        self._gempy_module = module
        return module

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, inputs: GemPyInputs) -> MeshRunResult:
        gp = self._import_gempy()
        timer = _Timer.start()

        if not inputs.formations:
            return MeshRunResult(
                engine=self.name,
                layers=[],
                duration_ms=timer.elapsed_ms,
                metadata={"reason": "no_formations"},
            )

        try:
            geomodel = self._build_geomodel(gp, inputs)
            gp.compute_model(geomodel)
        except GemPyUnavailableError:
            raise
        except Exception as exc:
            raise ModelingError(f"GemPy compute_model failed: {exc}") from exc

        try:
            layers = self._extract_layers(geomodel, inputs)
        except Exception as exc:  # pragma: no cover - solver-specific
            raise ModelingError(f"Could not extract surfaces from GemPy: {exc}") from exc

        return MeshRunResult(
            engine=self.name,
            layers=layers,
            duration_ms=timer.elapsed_ms,
            metadata={
                "refinement": self._config.refinement,
                "formation_count": len(layers),
                "vertex_count": int(sum(layer.vertex_count() for layer in layers)),
                "face_count": int(sum(layer.face_count() for layer in layers)),
            },
        )

    # ------------------------------------------------------------------
    # GemPy plumbing
    # ------------------------------------------------------------------

    def _build_geomodel(self, gp: ModuleType, inputs: GemPyInputs) -> Any:
        """Translate :class:`GemPyInputs` into a configured GemPy model."""

        extent = inputs.extent
        bounds = [
            extent.x_min,
            extent.x_max,
            extent.y_min,
            extent.y_max,
            extent.z_min,
            extent.z_max,
        ]
        # GemPy 2024.2 exposes both ``create_geomodel`` (factory) and the
        # ``data.GeoModel`` ctor; the factory is the documented entrypoint.
        geomodel = gp.create_geomodel(
            project_name=self._config.project_name,
            extent=bounds,
            resolution=[
                inputs.grid_resolution.nx,
                inputs.grid_resolution.ny,
                inputs.grid_resolution.nz,
            ],
            refinement=self._config.refinement,
        )

        # Stratigraphic order: GemPy stacks oldest at bottom of the column
        # (highest index in their convention). Our schema already encodes
        # that ordering directly.
        formations_sorted = sorted(
            inputs.formations, key=lambda f: f.stratigraphic_order, reverse=True
        )
        formation_names = [f.name for f in formations_sorted]
        try:
            gp.add_structural_group(
                geomodel,
                group_name="default",
                elements=formation_names,
            )
        except AttributeError:  # pragma: no cover - older GemPy fallback
            geomodel.structural_frame.append_group(formation_names)

        # Surface points
        sp_xyz = np.array(
            [(p.x, p.y, p.z) for p in inputs.surface_points], dtype=np.float64
        )
        sp_names = [
            self._formation_name_for(inputs, p.formation_id)
            for p in inputs.surface_points
        ]
        try:
            gp.add_surface_points(
                geomodel,
                x=sp_xyz[:, 0].tolist(),
                y=sp_xyz[:, 1].tolist(),
                z=sp_xyz[:, 2].tolist(),
                elements_names=sp_names,
            )
        except Exception as exc:  # pragma: no cover - GemPy-specific
            raise ModelingError(f"Failed to add surface points to GemPy: {exc}") from exc

        # Orientations
        if inputs.orientations:
            ori_xyz = np.array(
                [(o.x, o.y, o.z) for o in inputs.orientations], dtype=np.float64
            )
            ori_dips = [o.dip_degrees for o in inputs.orientations]
            ori_az = [o.azimuth_degrees for o in inputs.orientations]
            ori_pol = [o.polarity for o in inputs.orientations]
            ori_names = [
                self._formation_name_for(inputs, o.formation_id)
                for o in inputs.orientations
            ]
            try:
                gp.add_orientations(
                    geomodel,
                    x=ori_xyz[:, 0].tolist(),
                    y=ori_xyz[:, 1].tolist(),
                    z=ori_xyz[:, 2].tolist(),
                    elements_names=ori_names,
                    pole_vector=None,
                    dip=ori_dips,
                    azimuth=ori_az,
                    polarity=ori_pol,
                )
            except Exception as exc:  # pragma: no cover - GemPy-specific
                raise ModelingError(
                    f"Failed to add orientations to GemPy: {exc}"
                ) from exc

        return geomodel

    @staticmethod
    def _formation_name_for(inputs: GemPyInputs, rock_id: str) -> str:
        for formation in inputs.formations:
            if formation.rock_id == rock_id:
                return formation.name
        return rock_id

    def _extract_layers(self, geomodel: Any, inputs: GemPyInputs) -> list[LayerMesh]:
        """Walk GemPy's solutions and pack them into :class:`LayerMesh`."""

        try:
            solutions = geomodel.solutions
        except AttributeError as exc:  # pragma: no cover - bad GemPy build
            raise ModelingError(
                "GemPy geomodel has no .solutions; compute_model probably failed."
            ) from exc

        meshed = getattr(solutions, "raw_arrays", None)
        if meshed is None:
            raise ModelingError("GemPy did not produce raw_arrays surfaces.")

        vertices_per_surface = getattr(meshed, "vertices", None) or []
        edges_per_surface = getattr(meshed, "edges", None) or []
        if not vertices_per_surface:
            raise ModelingError("GemPy returned zero surface meshes.")

        formations_sorted = sorted(
            inputs.formations, key=lambda f: f.stratigraphic_order
        )
        layers: list[LayerMesh] = []
        # GemPy outputs surfaces in the *interface* order — between each
        # pair of stacked formations. We pair them with the underlying
        # (older) formation so the colour and id match the layer below.
        for surface_idx, (verts, faces) in enumerate(
            zip(vertices_per_surface, edges_per_surface, strict=False)
        ):
            if surface_idx >= len(formations_sorted) - 1:
                break
            formation = formations_sorted[surface_idx]
            color = formation.color_hex or _ROCK_TYPE_DEFAULT_COLOR.get(
                formation.rock_type, "#808080"
            )
            layers.append(
                LayerMesh(
                    surface_id=f"S_{formation.rock_id}",
                    name=formation.name,
                    rock_type=formation.rock_type,
                    color_hex=color,
                    vertices=np.asarray(verts, dtype=np.float64),
                    faces=np.asarray(faces, dtype=np.int32),
                )
            )
        return layers


__all__ = ["GemPyRunner", "GemPyRunnerConfig"]
