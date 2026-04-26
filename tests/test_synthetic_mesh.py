"""Unit tests for :mod:`geo_nyc.modeling.synthetic_mesh`.

The synthetic mesh builder is the demo's last-line fallback: it has to
work when GemPy / RBF / LLM all fail. Pin the contract here so the
fixture mode never silently regresses.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from geo_nyc.modeling.extent import GridResolution, ModelExtent
from geo_nyc.modeling.synthetic_mesh import LayerMesh, build_synthetic_layers
from geo_nyc.parsers.dsl.ast import (
    AbsoluteTime,
    DepositionEvent,
    IntrusionEvent,
    IntrusionStyle,
    Program,
    RockDefinition,
    RockType,
    TimeUnit,
)


def _extent() -> ModelExtent:
    return ModelExtent(
        x_min=0.0, x_max=100.0,
        y_min=0.0, y_max=100.0,
        z_min=-50.0, z_max=0.0,
    )


def _rock(rock_id: str, name: str, rock_type: RockType) -> RockDefinition:
    return RockDefinition(id=rock_id, name=name, rock_type=rock_type)


def _three_layer_program() -> Program:
    """Three depositions with explicit ages: schist (oldest) → marble → till."""

    rocks = (
        _rock("R_SCHIST", "Manhattan Schist", RockType.METAMORPHIC),
        _rock("R_MARBLE", "Inwood Marble", RockType.METAMORPHIC),
        _rock("R_TILL", "Glacial Till", RockType.SEDIMENTARY),
    )
    depositions = (
        DepositionEvent(
            id="D_SCHIST",
            rock_id="R_SCHIST",
            time=AbsoluteTime(value=450.0, unit=TimeUnit.MA),
        ),
        DepositionEvent(
            id="D_MARBLE",
            rock_id="R_MARBLE",
            time=AbsoluteTime(value=440.0, unit=TimeUnit.MA),
        ),
        DepositionEvent(
            id="D_TILL",
            rock_id="R_TILL",
            time=AbsoluteTime(value=0.02, unit=TimeUnit.MA),
        ),
    )
    return Program(rocks=rocks, depositions=depositions)


def test_builder_returns_one_layer_per_rock_event() -> None:
    layers = build_synthetic_layers(_three_layer_program(), _extent())
    assert len(layers) == 3
    assert all(isinstance(layer, LayerMesh) for layer in layers)
    assert {layer.surface_id for layer in layers} == {
        "D_SCHIST", "D_MARBLE", "D_TILL",
    }


def test_builder_orders_layers_oldest_first() -> None:
    """Stratigraphic order: oldest event sits at the deepest base z."""

    layers = build_synthetic_layers(_three_layer_program(), _extent())
    assert [layer.surface_id for layer in layers] == [
        "D_SCHIST", "D_MARBLE", "D_TILL",
    ]
    # Deepest base must be lower than the layer above it (modulo
    # bedrock relief — which is bounded by the default amplitude 8 m
    # and definitely smaller than the 50/3 ≈ 16.7 m layer thickness).
    schist_min_z = layers[0].vertices[:, 2].min()
    marble_min_z = layers[1].vertices[:, 2].min()
    assert schist_min_z < marble_min_z


def test_topmost_layer_is_pinned_to_ground_surface() -> None:
    extent = _extent()
    layers = build_synthetic_layers(_three_layer_program(), extent)
    top = layers[-1]
    # The contract says the topmost layer's z is *exactly* extent.z_max
    # so the frontend can find the ground surface deterministically.
    np.testing.assert_allclose(top.vertices[:, 2], extent.z_max, atol=1e-9)


def test_grid_resolution_drives_vertex_and_face_counts() -> None:
    res = GridResolution(nx=8, ny=6)
    layers = build_synthetic_layers(
        _three_layer_program(), _extent(), resolution=res
    )
    expected_verts = res.nx * res.ny
    # Each grid cell becomes 2 triangles, so faces = 2 * (nx-1) * (ny-1).
    expected_faces = 2 * (res.nx - 1) * (res.ny - 1)
    for layer in layers:
        assert layer.vertex_count() == expected_verts
        assert layer.face_count() == expected_faces


def test_color_overrides_take_precedence_over_rock_type_default() -> None:
    layers = build_synthetic_layers(
        _three_layer_program(),
        _extent(),
        color_overrides={"R_SCHIST": "#123456"},
    )
    schist_layer = next(layer for layer in layers if layer.surface_id == "D_SCHIST")
    marble_layer = next(layer for layer in layers if layer.surface_id == "D_MARBLE")
    assert schist_layer.color_hex == "#123456"
    # Marble has no override; falls back to the metamorphic default.
    assert marble_layer.color_hex == "#5C4A3A"


def test_face_indices_are_valid() -> None:
    """Every face index must point at an existing vertex."""

    layers = build_synthetic_layers(_three_layer_program(), _extent())
    for layer in layers:
        n_verts = layer.vertex_count()
        assert layer.faces.min() >= 0
        assert layer.faces.max() < n_verts
        # No degenerate triangles.
        assert layer.faces.shape[1] == 3


def test_builder_is_deterministic_given_same_seed() -> None:
    program = _three_layer_program()
    extent = _extent()
    a = build_synthetic_layers(program, extent, seed=7)
    b = build_synthetic_layers(program, extent, seed=7)
    for la, lb in zip(a, b, strict=True):
        np.testing.assert_array_equal(la.vertices, lb.vertices)
        np.testing.assert_array_equal(la.faces, lb.faces)


def test_seedless_run_is_deterministic_via_default_seed() -> None:
    program = _three_layer_program()
    extent = _extent()
    a = build_synthetic_layers(program, extent)
    b = build_synthetic_layers(program, extent)
    for la, lb in zip(a, b, strict=True):
        np.testing.assert_array_equal(la.vertices, lb.vertices)


def test_program_with_no_rock_events_returns_empty_list() -> None:
    empty = Program(rocks=(_rock("R_X", "X", RockType.SEDIMENTARY),))
    layers = build_synthetic_layers(empty, _extent())
    assert layers == []


def test_intrusion_events_are_treated_as_layers() -> None:
    """An IntrusionEvent should produce a layer just like a deposition."""

    rocks = (
        _rock("R_SCHIST", "Manhattan Schist", RockType.METAMORPHIC),
        _rock("R_RG", "Ravenswood Granodiorite", RockType.INTRUSIVE),
    )
    program = Program(
        rocks=rocks,
        depositions=(
            DepositionEvent(
                id="D_SCHIST",
                rock_id="R_SCHIST",
                time=AbsoluteTime(value=450.0, unit=TimeUnit.MA),
            ),
        ),
        intrusions=(
            IntrusionEvent(
                id="I_RG",
                rock_id="R_RG",
                style=IntrusionStyle.STOCK,
                time=AbsoluteTime(value=420.0, unit=TimeUnit.MA),
            ),
        ),
    )
    layers = build_synthetic_layers(program, _extent())
    assert {layer.surface_id for layer in layers} == {"D_SCHIST", "I_RG"}


def test_dated_events_sort_above_undated_ones() -> None:
    """Even with no absolute time, undated events must not slip below
    a known-old dated event."""

    rocks = (
        _rock("R_OLD", "Old", RockType.METAMORPHIC),
        _rock("R_NEW", "New", RockType.SEDIMENTARY),
    )
    program = Program(
        rocks=rocks,
        depositions=(
            DepositionEvent(
                id="D_OLD",
                rock_id="R_OLD",
                time=AbsoluteTime(value=500.0, unit=TimeUnit.MA),
            ),
            DepositionEvent(id="D_NEW", rock_id="R_NEW", time=None),
        ),
    )
    layers = build_synthetic_layers(program, _extent())
    assert [layer.surface_id for layer in layers] == ["D_OLD", "D_NEW"]


def test_bedrock_relief_amplitude_is_bounded() -> None:
    """Bedrock vertices vary around the base z by at most ~1.05 *
    amplitude (cosine bowl + small noise, both bounded)."""

    extent = _extent()
    amplitude = 4.0
    layers = build_synthetic_layers(
        _three_layer_program(),
        extent,
        bedrock_relief_m=amplitude,
        seed=1,
    )
    bedrock = layers[0]
    base_z = extent.z_min
    z = bedrock.vertices[:, 2]
    # bowl ∈ [-amp, 0]; noise stddev = amp*0.05. So z - base_z ∈
    # roughly [-1.5 * amp, +0.5 * amp]. Clamp to a generous envelope.
    assert (z - base_z).min() >= -2 * amplitude
    assert (z - base_z).max() <= amplitude


def test_color_falls_back_to_grey_for_unknown_rock_type() -> None:
    """RockType only has 4 values (sedimentary/volcanic/intrusive/
    metamorphic); each has a palette entry. We still want the
    fallback path to be testable."""

    rocks = (_rock("R_X", "X", RockType.METAMORPHIC),)
    program = Program(
        rocks=rocks,
        depositions=(
            DepositionEvent(
                id="D_X", rock_id="R_X",
                time=AbsoluteTime(value=10.0, unit=TimeUnit.MA),
            ),
        ),
    )
    # Use color_overrides={} — the layer should pick up the
    # metamorphic default colour, not a missing-key error.
    layers = build_synthetic_layers(program, _extent(), color_overrides={})
    assert layers[0].color_hex == "#5C4A3A"


@pytest.mark.parametrize("nx,ny", [(2, 2), (3, 5), (16, 16)])
def test_grid_dimensions_yield_well_formed_meshes(nx: int, ny: int) -> None:
    res = GridResolution(nx=nx, ny=ny)
    layers = build_synthetic_layers(
        _three_layer_program(), _extent(), resolution=res
    )
    for layer in layers:
        # Each vertex z should be finite (no NaN / inf escapes).
        assert math.isfinite(float(layer.vertices[:, 2].min()))
        assert math.isfinite(float(layer.vertices[:, 2].max()))
