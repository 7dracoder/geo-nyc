"""Unit tests for :mod:`geo_nyc.modeling.rbf_runner`."""

from __future__ import annotations

import json

import numpy as np
import pytest

from geo_nyc.config import REPO_ROOT
from geo_nyc.modeling import ConstraintBuilder, RBFRunner, RBFRunnerConfig
from geo_nyc.modeling.constraints import (
    ExtentBox,
    FormationConstraint,
    GemPyInputs,
    GridResolution3D,
    Orientation,
    SurfacePoint,
)
from geo_nyc.parsers.dsl import parse_and_validate
from geo_nyc.runs.fixtures import load_fixture_bundle


@pytest.fixture
def fixture_inputs() -> GemPyInputs:
    bundle = load_fixture_bundle(REPO_ROOT / "data" / "fixtures", "nyc_demo")
    program, _ = parse_and_validate(bundle.dsl_text)
    return ConstraintBuilder().build(
        program=program,
        extent=bundle.extent,
        crs=bundle.crs,
        fixture_extraction=bundle.extraction,
    )


def test_runner_produces_one_layer_per_formation(fixture_inputs: GemPyInputs) -> None:
    result = RBFRunner(RBFRunnerConfig(grid_nx=16, grid_ny=16)).run(fixture_inputs)
    assert result.engine == "rbf"
    assert len(result.layers) == len(fixture_inputs.formations)
    # Each layer is a closed slab: top grid + bottom grid + side walls,
    # so the vertex count is 2 * nx * ny.
    assert all(layer.vertex_count() == 2 * 16 * 16 for layer in result.layers)
    assert result.duration_ms >= 0


def test_runner_returns_layers_in_stratigraphic_order(
    fixture_inputs: GemPyInputs,
) -> None:
    result = RBFRunner(RBFRunnerConfig(grid_nx=8, grid_ny=8)).run(fixture_inputs)
    surface_ids = [layer.surface_id for layer in result.layers]
    # Oldest first → R_SCHIST → ... → R_FILL.
    assert surface_ids[0].endswith("R_SCHIST")
    assert surface_ids[-1].endswith("R_FILL")


def _top_z_of_slab(layer, grid_nx: int, grid_ny: int) -> np.ndarray:
    """First half of slab vertices = the geological top surface."""

    n_grid = grid_nx * grid_ny
    return layer.vertices[:n_grid, 2]


def test_runner_respects_fixture_horizon_depths(
    fixture_inputs: GemPyInputs,
) -> None:
    result = RBFRunner(RBFRunnerConfig(grid_nx=16, grid_ny=16)).run(fixture_inputs)
    rock_to_layer = {layer.surface_id: layer for layer in result.layers}

    # Bedrock *top* should have significant spatial variation (shallow
    # at edges, deep in the middle) — the enriched fixture has bedrock
    # ranging from -15 m to -80 m across the AOI. We slice the top
    # half of the slab because the slab also packs a flat extent-floor
    # surface underneath each formation.
    schist = rock_to_layer["S_R_SCHIST"]
    schist_top_z = _top_z_of_slab(schist, 16, 16)
    z_range = float(schist_top_z.max() - schist_top_z.min())
    assert z_range > 20.0, f"Expected significant bedrock relief, got range={z_range:.1f} m"
    # Mean should be somewhere in the middle of the fixture range.
    assert -70.0 < schist_top_z.mean() < -20.0

    # Ground surface (top-of-fill) should still be near 0.
    fill = rock_to_layer["S_R_FILL"]
    assert _top_z_of_slab(fill, 16, 16).mean() == pytest.approx(0.0, abs=0.5)


def test_stack_order_is_strictly_monotonic(fixture_inputs: GemPyInputs) -> None:
    result = RBFRunner(RBFRunnerConfig(grid_nx=12, grid_ny=12)).run(fixture_inputs)
    older_top_z = _top_z_of_slab(result.layers[0], 12, 12)
    for younger in result.layers[1:]:
        younger_top_z = _top_z_of_slab(younger, 12, 12)
        assert (younger_top_z >= older_top_z - 1e-6).all(), (
            "Younger formation top dipped below the older top underneath."
        )
        older_top_z = younger_top_z


def test_empty_inputs_returns_no_layers() -> None:
    extent = ExtentBox(x_min=0, x_max=1, y_min=0, y_max=1, z_min=-10, z_max=0)
    inputs = GemPyInputs(extent=extent, crs="EPSG:32618")
    result = RBFRunner().run(inputs)
    assert result.layers == []
    assert result.metadata["reason"] == "no_formations"


def test_few_anchors_falls_back_to_constant_mean() -> None:
    extent = ExtentBox(x_min=0, x_max=100, y_min=0, y_max=100, z_min=-50, z_max=0)
    inputs = GemPyInputs(
        extent=extent,
        crs="EPSG:32618",
        formations=[
            FormationConstraint(
                rock_id="R_A",
                name="Layer A",
                rock_type="metamorphic",
                stratigraphic_order=0,
                source="fixture",
            ),
            FormationConstraint(
                rock_id="R_B",
                name="Layer B",
                rock_type="sedimentary",
                stratigraphic_order=1,
                source="fixture",
            ),
        ],
        # Only 1 surface point per formation → too few for an RBF; we
        # expect a flat plane at the anchor's z.
        surface_points=[
            SurfacePoint(formation_id="R_A", x=50, y=50, z=-30),
            SurfacePoint(formation_id="R_B", x=50, y=50, z=-5),
        ],
        orientations=[
            Orientation(
                formation_id="R_A",
                x=50,
                y=50,
                z=-30,
                dip_degrees=2.0,
                azimuth_degrees=90.0,
            )
        ],
    )

    result = RBFRunner(RBFRunnerConfig(grid_nx=8, grid_ny=8)).run(inputs)
    assert len(result.layers) == 2
    n_grid = 8 * 8
    # Top half of each slab = the top-of-formation surface.
    a_top_z = result.layers[0].vertices[:n_grid, 2]
    b_top_z = result.layers[1].vertices[:n_grid, 2]
    assert np.allclose(a_top_z, -30.0)
    assert np.allclose(b_top_z, -5.0)
    # Bottom half of the deepest slab = extent floor; bottom of the
    # younger slab = top of the older one underneath.
    a_bot_z = result.layers[0].vertices[n_grid:, 2]
    b_bot_z = result.layers[1].vertices[n_grid:, 2]
    assert np.allclose(a_bot_z, extent.z_min)
    assert np.allclose(b_bot_z, -30.0)
    layer_meta = result.metadata["layers"]
    assert layer_meta[0]["interpolation"] == "constant_mean"
    assert layer_meta[1]["interpolation"] == "constant_mean"


def test_runner_clamps_to_extent() -> None:
    extent = ExtentBox(x_min=0, x_max=10, y_min=0, y_max=10, z_min=-100, z_max=0)
    inputs = GemPyInputs(
        extent=extent,
        crs="EPSG:32618",
        grid_resolution=GridResolution3D(),
        formations=[
            FormationConstraint(
                rock_id="R_A",
                name="A",
                rock_type="metamorphic",
                stratigraphic_order=0,
                source="fixture",
            )
        ],
        # Three points all at -50 metres but with one wild outlier
        # outside the extent: the runner must clip.
        surface_points=[
            SurfacePoint(formation_id="R_A", x=0, y=0, z=-1000.0),
            SurfacePoint(formation_id="R_A", x=10, y=0, z=-50.0),
            SurfacePoint(formation_id="R_A", x=0, y=10, z=-50.0),
            SurfacePoint(formation_id="R_A", x=10, y=10, z=-50.0),
        ],
        orientations=[
            Orientation(
                formation_id="R_A",
                x=5,
                y=5,
                z=-50,
                dip_degrees=2.0,
                azimuth_degrees=90.0,
            )
        ],
    )

    result = RBFRunner(RBFRunnerConfig(grid_nx=8, grid_ny=8)).run(inputs)
    z = result.layers[0].vertices[:, 2]
    assert (z >= extent.z_min - 1e-9).all()
    assert (z <= extent.z_max + 1e-9).all()


def test_round_trip_through_gempy_inputs_json(fixture_inputs: GemPyInputs) -> None:
    """Re-loading the persisted gempy_inputs.json must work end-to-end."""

    raw = fixture_inputs.model_dump_json()
    reloaded = GemPyInputs.model_validate_json(raw)
    assert json.loads(raw) == json.loads(reloaded.model_dump_json())

    result = RBFRunner(RBFRunnerConfig(grid_nx=8, grid_ny=8)).run(reloaded)
    assert len(result.layers) == len(reloaded.formations)
