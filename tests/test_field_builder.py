"""Unit tests for :mod:`geo_nyc.modeling.field_builder`."""

from __future__ import annotations

import numpy as np
import pytest

from geo_nyc.modeling.constraints import (
    ExtentBox,
    FormationConstraint,
    GemPyInputs,
    SurfacePoint,
)
from geo_nyc.modeling.extent import ModelExtent
from geo_nyc.modeling.field_builder import (
    FieldBuilderConfig,
    build_depth_to_bedrock_field_from_inputs,
    build_stub_depth_field,
)


def _make_extent_box() -> ExtentBox:
    return ExtentBox(
        x_min=580_000.0,
        x_max=583_000.0,
        y_min=4_507_000.0,
        y_max=4_510_000.0,
        z_min=-100.0,
        z_max=0.0,
    )


def _bedrock_inputs(z_values: list[float]) -> GemPyInputs:
    """Inputs with a single bedrock formation and ``len(z_values)`` anchors.

    Anchors are arranged on a 2D grid that spans the extent so the
    thin-plate RBF has an interior to interpolate, not just a 1D
    diagonal. The exact grid is chosen so any prefix of ``z_values``
    is still 2D-distributed.
    """

    extent = _make_extent_box()
    formations = [
        FormationConstraint(
            rock_id="R_SCHIST",
            name="Manhattan Schist",
            rock_type="metamorphic",
            stratigraphic_order=0,
        ),
        FormationConstraint(
            rock_id="R_TILL",
            name="Glacial Till",
            rock_type="sedimentary",
            stratigraphic_order=1,
        ),
    ]
    anchor_template = [
        (extent.x_min + 200, extent.y_min + 200),
        (extent.x_max - 200, extent.y_min + 200),
        (extent.x_max - 200, extent.y_max - 200),
        (extent.x_min + 200, extent.y_max - 200),
        ((extent.x_min + extent.x_max) / 2, (extent.y_min + extent.y_max) / 2),
        ((extent.x_min + extent.x_max) / 2, extent.y_min + 300),
        ((extent.x_min + extent.x_max) / 2, extent.y_max - 300),
        (extent.x_min + 300, (extent.y_min + extent.y_max) / 2),
    ]
    surface_points = [
        SurfacePoint(formation_id="R_SCHIST", x=float(x), y=float(y), z=float(z))
        for (x, y), z in zip(anchor_template[: len(z_values)], z_values, strict=True)
    ]
    return GemPyInputs(
        extent=extent,
        crs="EPSG:32618",
        formations=formations,
        surface_points=surface_points,
    )


def test_real_builder_produces_correct_shape_and_units() -> None:
    inputs = _bedrock_inputs([-30.0, -28.0, -27.0, -29.0])
    cfg = FieldBuilderConfig(grid_nx=16, grid_ny=12)

    field = build_depth_to_bedrock_field_from_inputs(inputs, config=cfg)

    assert field.values.shape == (12, 16)
    assert field.values.dtype == np.float32
    assert field.units == "meters_below_surface"
    assert field.crs == "EPSG:32618"
    assert field.source == "rbf"
    assert field.resolution_m is not None and field.resolution_m > 0
    assert field.values.min() >= cfg.min_overburden_m
    # Mean depth should track the anchors' mean (~28 m).
    assert 20.0 < field.values.mean() < 40.0


def test_real_builder_picks_metamorphic_when_orders_tie() -> None:
    extent = _make_extent_box()
    formations = [
        FormationConstraint(
            rock_id="R_OTHER",
            name="Outwash",
            rock_type="sedimentary",
            stratigraphic_order=0,
        ),
        FormationConstraint(
            rock_id="R_SCHIST",
            name="Manhattan Schist",
            rock_type="metamorphic",
            stratigraphic_order=0,
        ),
    ]
    surface_points = [
        SurfacePoint(formation_id="R_SCHIST", x=581_000.0, y=4_507_500.0, z=-50.0),
        SurfacePoint(formation_id="R_SCHIST", x=582_500.0, y=4_508_000.0, z=-50.0),
        SurfacePoint(formation_id="R_SCHIST", x=580_500.0, y=4_509_000.0, z=-50.0),
        SurfacePoint(formation_id="R_SCHIST", x=581_500.0, y=4_509_500.0, z=-50.0),
        SurfacePoint(formation_id="R_OTHER", x=581_000.0, y=4_508_000.0, z=-5.0),
    ]
    inputs = GemPyInputs(
        extent=extent,
        crs="EPSG:32618",
        formations=formations,
        surface_points=surface_points,
    )

    field = build_depth_to_bedrock_field_from_inputs(
        inputs, config=FieldBuilderConfig(grid_nx=8, grid_ny=8)
    )

    # If we'd picked R_OTHER (overburden), depth would be ~5 m. Schist
    # is 50 m down, so values should sit there.
    assert 40.0 < field.values.mean() < 55.0
    assert field.source == "rbf"


def test_real_builder_few_anchors_falls_back_to_constant_mean() -> None:
    inputs = _bedrock_inputs([-12.0])  # single anchor -> constant_mean
    cfg = FieldBuilderConfig(grid_nx=8, grid_ny=8, min_points_for_rbf=3)

    field = build_depth_to_bedrock_field_from_inputs(inputs, config=cfg)

    # Single anchor at z=-12 -> uniform depth 12 m, clamped to floor.
    assert pytest.approx(field.values.min(), abs=0.1) == 12.0
    assert pytest.approx(field.values.max(), abs=0.1) == 12.0
    assert field.source == "rbf"


def test_real_builder_no_bedrock_anchors_returns_stub() -> None:
    extent = _make_extent_box()
    formations = [
        FormationConstraint(
            rock_id="R_SCHIST",
            name="Manhattan Schist",
            rock_type="metamorphic",
            stratigraphic_order=0,
        ),
    ]
    inputs = GemPyInputs(
        extent=extent,
        crs="EPSG:32618",
        formations=formations,
        surface_points=[],  # no anchors at all
    )

    field = build_depth_to_bedrock_field_from_inputs(
        inputs, config=FieldBuilderConfig(grid_nx=8, grid_ny=8)
    )

    # constant_midplane => extent z=-50 => depth 50 m, but tagged stub.
    assert field.source == "stub"
    assert field.values.min() >= 0.0


def test_real_builder_no_formations_returns_stub() -> None:
    extent = _make_extent_box()
    inputs = GemPyInputs(extent=extent, crs="EPSG:32618")
    field = build_depth_to_bedrock_field_from_inputs(
        inputs, config=FieldBuilderConfig(grid_nx=8, grid_ny=8)
    )
    assert field.source == "stub"


def test_stub_field_is_deterministic_and_within_extent() -> None:
    extent = ModelExtent(
        x_min=580_000.0,
        x_max=583_000.0,
        y_min=4_507_000.0,
        y_max=4_510_000.0,
        z_min=-100.0,
        z_max=0.0,
    )
    cfg = FieldBuilderConfig(grid_nx=10, grid_ny=10)

    a = build_stub_depth_field(extent, config=cfg)
    b = build_stub_depth_field(extent, config=cfg)

    np.testing.assert_array_equal(a.values, b.values)
    assert a.source == "stub"
    assert a.units == "meters_below_surface"
    assert a.values.shape == (10, 10)
    assert a.values.min() >= cfg.min_overburden_m
    # Stub is centred near 25 m with ±~14 m wobble.
    assert 5.0 < a.values.mean() < 50.0


def test_stub_field_seed_changes_output_but_keeps_shape() -> None:
    extent = ModelExtent(
        x_min=0.0, x_max=100.0,
        y_min=0.0, y_max=100.0,
        z_min=-50.0, z_max=0.0,
    )
    cfg = FieldBuilderConfig(grid_nx=8, grid_ny=8)
    a = build_stub_depth_field(extent, config=cfg, seed=0)
    b = build_stub_depth_field(extent, config=cfg, seed=42)
    assert a.values.shape == b.values.shape
    # seed=42 perturbation is small but nonzero
    assert not np.array_equal(a.values, b.values)
    assert a.source == b.source == "stub"
