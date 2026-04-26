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
    assert all(layer.vertex_count() == 16 * 16 for layer in result.layers)
    assert result.duration_ms >= 0


def test_runner_returns_layers_in_stratigraphic_order(
    fixture_inputs: GemPyInputs,
) -> None:
    result = RBFRunner(RBFRunnerConfig(grid_nx=8, grid_ny=8)).run(fixture_inputs)
    surface_ids = [layer.surface_id for layer in result.layers]
    # Oldest first → R_SCHIST → ... → R_FILL.
    assert surface_ids[0].endswith("R_SCHIST")
    assert surface_ids[-1].endswith("R_FILL")


def test_runner_respects_fixture_horizon_depths(
    fixture_inputs: GemPyInputs,
) -> None:
    result = RBFRunner(RBFRunnerConfig(grid_nx=16, grid_ny=16)).run(fixture_inputs)
    rock_to_layer = {layer.surface_id: layer for layer in result.layers}

    # Bedrock top should have significant spatial variation (shallow at
    # edges, deep in the middle) — the enriched fixture has bedrock
    # ranging from -15 m to -80 m across the AOI.
    schist = rock_to_layer["S_R_SCHIST"]
    schist_z = schist.vertices[:, 2]
    z_range = float(schist_z.max() - schist_z.min())
    assert z_range > 20.0, f"Expected significant bedrock relief, got range={z_range:.1f} m"
    # Mean should be somewhere in the middle of the fixture range.
    assert -70.0 < schist_z.mean() < -20.0

    # Ground surface (top-of-fill) should still be near 0.
    fill = rock_to_layer["S_R_FILL"]
    assert fill.vertices[:, 2].mean() == pytest.approx(0.0, abs=0.5)


def test_stack_order_is_strictly_monotonic(fixture_inputs: GemPyInputs) -> None:
    result = RBFRunner(RBFRunnerConfig(grid_nx=12, grid_ny=12)).run(fixture_inputs)
    older_z = result.layers[0].vertices[:, 2]
    for younger in result.layers[1:]:
        younger_z = younger.vertices[:, 2]
        assert (younger_z >= older_z - 1e-6).all(), (
            "Younger formation surface dipped below the older one underneath."
        )
        older_z = younger_z


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
    a_z = result.layers[0].vertices[:, 2]
    b_z = result.layers[1].vertices[:, 2]
    assert np.allclose(a_z, -30.0)
    assert np.allclose(b_z, -5.0)
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
