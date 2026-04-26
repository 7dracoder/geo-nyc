"""Unit tests for the GemPy-input Pydantic schemas (Phase 7.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from geo_nyc.modeling.constraints import (
    ExtentBox,
    FormationConstraint,
    GemPyInputs,
    GridResolution3D,
    Orientation,
    SurfacePoint,
)


def _formation(**kwargs):
    base = dict(
        rock_id="R_TEST",
        name="Test Schist",
        rock_type="metamorphic",
        stratigraphic_order=0,
    )
    base.update(kwargs)
    return FormationConstraint(**base)


def test_formation_constraint_round_trips() -> None:
    f = _formation(color_hex="#abcdef", source="extracted", age_ma=450.0)
    payload = f.model_dump()
    assert FormationConstraint.model_validate(payload) == f


def test_orientation_rejects_polarity_zero() -> None:
    with pytest.raises(ValidationError):
        Orientation(
            formation_id="R_TEST",
            x=0.0,
            y=0.0,
            z=0.0,
            dip_degrees=10.0,
            azimuth_degrees=90.0,
            polarity=0,
        )


def test_orientation_rejects_dip_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Orientation(
            formation_id="R_TEST",
            x=0.0,
            y=0.0,
            z=0.0,
            dip_degrees=120.0,
            azimuth_degrees=90.0,
        )


def test_extent_box_rejects_inverted_axes() -> None:
    with pytest.raises(ValidationError):
        ExtentBox(
            x_min=10.0,
            x_max=0.0,
            y_min=0.0,
            y_max=10.0,
            z_min=-10.0,
            z_max=0.0,
        )


def test_surface_point_rejects_long_evidence() -> None:
    with pytest.raises(ValidationError):
        SurfacePoint(
            formation_id="R_TEST",
            x=0.0,
            y=0.0,
            z=0.0,
            evidence_quote="x" * 700,
        )


def test_demo_ready_threshold() -> None:
    extent = ExtentBox(x_min=0, x_max=1, y_min=0, y_max=1, z_min=-10, z_max=0)
    inputs = GemPyInputs(extent=extent, crs="EPSG:32618")
    assert inputs.is_demo_ready() is False

    inputs2 = GemPyInputs(
        extent=extent,
        crs="EPSG:32618",
        formations=[
            _formation(rock_id="R_A", stratigraphic_order=0),
            _formation(rock_id="R_B", stratigraphic_order=1),
        ],
        surface_points=[
            SurfacePoint(formation_id="R_A", x=0, y=0, z=-1),
            SurfacePoint(formation_id="R_B", x=0, y=0, z=-2),
        ],
        orientations=[
            Orientation(
                formation_id="R_A",
                x=0,
                y=0,
                z=-1,
                dip_degrees=2.0,
                azimuth_degrees=90.0,
            )
        ],
    )
    assert inputs2.is_demo_ready() is True


def test_grid_resolution_defaults_and_bounds() -> None:
    g = GridResolution3D()
    assert (g.nx, g.ny, g.nz) == (32, 32, 32)
    with pytest.raises(ValidationError):
        GridResolution3D(nx=1)
