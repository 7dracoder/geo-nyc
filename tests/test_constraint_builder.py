"""Unit tests for the constraint builder (Phase 7.2)."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from geo_nyc.config import REPO_ROOT
from geo_nyc.extraction.structured import LLMExtraction
from geo_nyc.modeling import ConstraintBuilder
from geo_nyc.modeling.constraints import GemPyInputs
from geo_nyc.modeling.extent import ModelExtent
from geo_nyc.parsers.dsl import (
    build_program_from_extraction,
    parse_and_validate,
    serialize,
)
from geo_nyc.runs.fixtures import load_fixture_bundle


@pytest.fixture
def fixture_bundle():
    fixtures_dir = REPO_ROOT / "data" / "fixtures"
    return load_fixture_bundle(fixtures_dir, "nyc_demo")


@pytest.fixture
def fixture_program(fixture_bundle):
    program, report = parse_and_validate(fixture_bundle.dsl_text)
    assert report.is_valid
    return program


@pytest.fixture
def llm_extraction() -> LLMExtraction:
    raw = json.loads(
        (REPO_ROOT / "data" / "fixtures" / "nyc_demo" / "llm_extraction.json").read_text(
            encoding="utf-8"
        )
    )
    return LLMExtraction.model_validate(raw)


def test_fixture_only_path_produces_demo_ready_inputs(
    fixture_bundle, fixture_program
) -> None:
    inputs = ConstraintBuilder().build(
        program=fixture_program,
        extent=fixture_bundle.extent,
        crs=fixture_bundle.crs,
        fixture_extraction=fixture_bundle.extraction,
    )

    assert isinstance(inputs, GemPyInputs)
    assert inputs.is_demo_ready()
    assert {f.rock_id for f in inputs.formations} == {
        "R_FILL",
        "R_TILL",
        "R_OUTWASH",
        "R_SCHIST",
    }
    # Surface points should anchor every formation; orientations 1:1.
    point_counts = Counter(p.formation_id for p in inputs.surface_points)
    assert all(v == 5 for v in point_counts.values()), point_counts
    assert {o.formation_id for o in inputs.orientations} == set(point_counts)
    # Provenance should be entirely fixture-sourced (no LLM data passed).
    assert all(p.source == "fixture" for p in inputs.surface_points)
    assert all(o.source == "fixture" for o in inputs.orientations)


def test_stratigraphic_order_is_oldest_first(fixture_bundle, fixture_program) -> None:
    inputs = ConstraintBuilder().build(
        program=fixture_program,
        extent=fixture_bundle.extent,
        crs=fixture_bundle.crs,
        fixture_extraction=fixture_bundle.extraction,
    )
    by_id = {f.rock_id: f for f in inputs.formations}
    # Schist was deposited first (D_SCHIST has after: ()) so it must be 0.
    assert by_id["R_SCHIST"].stratigraphic_order == 0
    # Fill is youngest (last in chain) → highest index.
    assert by_id["R_FILL"].stratigraphic_order == 3


def test_horizons_pin_formation_tops(fixture_bundle, fixture_program) -> None:
    inputs = ConstraintBuilder().build(
        program=fixture_program,
        extent=fixture_bundle.extent,
        crs=fixture_bundle.crs,
        fixture_extraction=fixture_bundle.extraction,
    )
    horizons = fixture_bundle.extraction["depth_horizons_m"]
    schist_points = [p for p in inputs.surface_points if p.formation_id == "R_SCHIST"]
    assert all(p.z == pytest.approx(horizons["bedrock_top"]) for p in schist_points)
    # Top of Glacial Till is the *base of the formation above* it
    # (outwash_base = -25 m), not till_base.
    till_points = [p for p in inputs.surface_points if p.formation_id == "R_TILL"]
    assert all(p.z == pytest.approx(horizons["outwash_base"]) for p in till_points)
    # Top of the youngest formation must be the ground surface.
    fill_points = [p for p in inputs.surface_points if p.formation_id == "R_FILL"]
    assert all(p.z == pytest.approx(horizons["ground_surface"]) for p in fill_points)


def test_llm_extracted_provenance_overrides_fixture(
    fixture_bundle, llm_extraction
) -> None:
    """When a contact has an extracted depth, it must dominate the inferred default."""

    # Build the LLM-derived program (Phase 6 path) and reparse so we get a
    # canonical AST.
    build_report = build_program_from_extraction(llm_extraction)
    program, _ = parse_and_validate(serialize(build_report.program))

    inputs = ConstraintBuilder().build(
        program=program,
        extent=fixture_bundle.extent,
        crs=fixture_bundle.crs,
        llm_extraction=llm_extraction,
        fixture_extraction=fixture_bundle.extraction,
    )

    point_sources = Counter(p.source for p in inputs.surface_points)
    assert point_sources["extracted"] >= 5  # 5 anchors per LLM-pinned formation
    # The Manhattan Schist top should sit at -42 m (one of the contacts).
    schist_pts = [p for p in inputs.surface_points if p.formation_id.endswith("MANHATTAN_SCHIST")]
    assert any(p.z == pytest.approx(-42.0) for p in schist_pts)
    # And there should be at least one extracted dip.
    extracted_dips = [o for o in inputs.orientations if o.source == "extracted"]
    assert extracted_dips
    assert all(0.0 <= o.azimuth_degrees < 360.0 for o in inputs.orientations)


def test_orientation_default_is_subhorizontal(fixture_bundle, fixture_program) -> None:
    inputs = ConstraintBuilder().build(
        program=fixture_program,
        extent=fixture_bundle.extent,
        crs=fixture_bundle.crs,
        fixture_extraction=fixture_bundle.extraction,
    )
    # Without any LLM-supplied dips, every orientation should hold the
    # default sub-horizontal anchor.
    for o in inputs.orientations:
        assert o.dip_degrees == pytest.approx(2.0)
        assert o.azimuth_degrees == pytest.approx(90.0)


def test_inputs_round_trip_through_json(fixture_bundle, fixture_program) -> None:
    inputs = ConstraintBuilder().build(
        program=fixture_program,
        extent=fixture_bundle.extent,
        crs=fixture_bundle.crs,
        fixture_extraction=fixture_bundle.extraction,
    )
    raw = inputs.model_dump_json()
    rehydrated = GemPyInputs.model_validate_json(raw)
    assert rehydrated == inputs


def test_empty_program_returns_empty_constraints(fixture_bundle) -> None:
    """A degenerate Program (no rocks) must not crash the builder."""

    from geo_nyc.parsers.dsl.ast import Program

    inputs = ConstraintBuilder().build(
        program=Program(),
        extent=fixture_bundle.extent,
        crs=fixture_bundle.crs,
    )
    assert inputs.formations == []
    assert inputs.surface_points == []
    assert inputs.orientations == []
    assert inputs.is_demo_ready() is False


def test_extracted_depth_is_clamped_to_extent() -> None:
    """A 9000 m depth in the LLM extraction must not punch through the extent floor."""

    extent = ModelExtent(
        x_min=0.0, x_max=100.0, y_min=0.0, y_max=100.0, z_min=-200.0, z_max=0.0
    )
    payload: dict[str, Any] = {
        "formations": [
            {
                "name": "Manhattan Schist",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [{"document_id": "d", "page": 1, "quote": "stub"}],
            },
            {
                "name": "Inwood Marble",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [{"document_id": "d", "page": 1, "quote": "stub"}],
            },
        ],
        "contacts": [
            {
                "top_formation": "Manhattan Schist",
                "bottom_formation": "Inwood Marble",
                "depth_value": 9000.0,
                "depth_unit": "m",
                "evidence": [{"document_id": "d", "page": 1, "quote": "stub"}],
            }
        ],
        "structures": [],
        "notes": None,
    }
    extraction = LLMExtraction.model_validate(payload)
    program, _ = parse_and_validate(
        serialize(build_program_from_extraction(extraction).program)
    )

    inputs = ConstraintBuilder().build(
        program=program,
        extent=extent,
        crs="EPSG:32618",
        llm_extraction=extraction,
    )

    for p in inputs.surface_points:
        assert extent.z_min <= p.z <= extent.z_max
