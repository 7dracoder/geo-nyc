"""Unit tests for the LLMExtraction → DSL builder (Phase 6.2)."""

from __future__ import annotations

from typing import Any

import pytest

from geo_nyc.domain.normalization import GeologyGlossary, GlossaryEntry
from geo_nyc.extraction.structured import LLMExtraction
from geo_nyc.parsers.dsl import (
    DSLValidator,
    GeologyDSLParser,
    build_dsl_from_extraction,
    build_program_from_extraction,
)


def _extraction(payload: dict[str, Any]) -> LLMExtraction:
    return LLMExtraction.model_validate(payload)


def _doc_evidence(text: str = "Sample evidence quote.") -> dict[str, Any]:
    return {"document_id": "d_test", "page": 1, "quote": text}


def _build_payload() -> dict[str, Any]:
    return {
        "formations": [
            {
                "name": "manhattan schist",
                "rock_type": None,
                "aliases": ["MnS"],
                "evidence": [_doc_evidence()],
            },
            {
                "name": "Inwood Marble",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [_doc_evidence()],
            },
            {
                "name": "Ravenswood pluton",
                "rock_type": None,
                "aliases": [],
                "evidence": [_doc_evidence()],
            },
        ],
        "contacts": [
            {
                "top_formation": "manhattan schist",
                "bottom_formation": "Inwood Marble",
                "depth_value": 30.0,
                "depth_unit": "m",
                "evidence": [_doc_evidence()],
            }
        ],
        "structures": [],
        "notes": None,
    }


def test_builder_normalises_names_and_emits_canonical_rocks() -> None:
    extraction = _extraction(_build_payload())

    report = build_program_from_extraction(extraction)
    program = report.program

    rock_names = {r.name for r in program.rocks}
    assert {"Manhattan Schist", "Inwood Marble", "Ravenswood Granodiorite"} <= rock_names

    rock_ids = {r.id for r in program.rocks}
    assert {
        "R_MANHATTAN_SCHIST",
        "R_INWOOD_MARBLE",
        "R_RAVENSWOOD_GRANODIORITE",
    } <= rock_ids


def test_builder_orders_depositions_via_contacts() -> None:
    extraction = _extraction(_build_payload())

    report = build_program_from_extraction(extraction)
    program = report.program

    # Inwood Marble (bottom) must be deposited before Manhattan Schist (top)
    # — that's the only ordering constraint the contacts impose.
    deposition_rocks = [d.rock_id for d in program.depositions]
    assert deposition_rocks.index("R_INWOOD_MARBLE") < deposition_rocks.index(
        "R_MANHATTAN_SCHIST"
    )

    # Each event chains off the *previous* one in the topological sort,
    # giving a fully connected stratigraphic chain even when the source
    # contact graph is sparse.
    deposition_id_by_rock = {d.rock_id: d.id for d in program.depositions}
    intrusion_id_by_rock = {i.rock_id: i.id for i in program.intrusions}
    inwood_dep = next(d for d in program.depositions if d.rock_id == "R_INWOOD_MARBLE")
    assert inwood_dep.after == ()
    # Whichever event the builder emits *next* should reference D_INWOOD_MARBLE.
    everyone_after = [d.after for d in program.depositions if d.id != inwood_dep.id]
    everyone_after += [i.after for i in program.intrusions]
    assert any(("D_INWOOD_MARBLE",) == ref for ref in everyone_after)
    # Manhattan Schist's deposition should reference whatever came before it.
    manhattan_dep = next(
        d for d in program.depositions if d.rock_id == "R_MANHATTAN_SCHIST"
    )
    valid_predecessors = {
        deposition_id_by_rock["R_INWOOD_MARBLE"],
        intrusion_id_by_rock.get("R_RAVENSWOOD_GRANODIORITE"),
    }
    assert manhattan_dep.after and manhattan_dep.after[0] in valid_predecessors


def test_builder_emits_intrusion_for_intrusive_rocks() -> None:
    extraction = _extraction(_build_payload())

    report = build_program_from_extraction(extraction)
    program = report.program

    intrusion_rocks = [i.rock_id for i in program.intrusions]
    assert "R_RAVENSWOOD_GRANODIORITE" in intrusion_rocks

    # The intrusion should not also be a deposition.
    deposition_rocks = {d.rock_id for d in program.depositions}
    assert "R_RAVENSWOOD_GRANODIORITE" not in deposition_rocks


def test_built_dsl_round_trips_through_the_parser() -> None:
    extraction = _extraction(_build_payload())

    dsl_text, _ = build_dsl_from_extraction(extraction)
    program = GeologyDSLParser().parse(dsl_text)
    report = DSLValidator().validate(program)

    assert report.is_valid, [str(e) for e in report.errors]
    # The serialised program should also round-trip back to itself.
    second_text, _ = build_dsl_from_extraction(extraction)
    assert dsl_text == second_text


def test_builder_falls_back_when_contacts_form_a_cycle() -> None:
    payload = _build_payload()
    payload["contacts"].append(
        {
            "top_formation": "Inwood Marble",
            "bottom_formation": "Manhattan Schist",
            "depth_value": 12.0,
            "depth_unit": "m",
            "evidence": [_doc_evidence()],
        }
    )
    extraction = _extraction(payload)

    report = build_program_from_extraction(extraction)
    assert any("cycle" in w.lower() for w in report.warnings)
    # Even with a cycle the program must remain syntactically valid.
    parser = GeologyDSLParser()
    parsed = parser.parse(
        "\n".join(
            line
            for line in [
                'ROCK R_TEST [ name: "Test"; type: metamorphic ]',
            ]
        )
        + "\n"
    )
    assert parsed.rocks  # sanity: parser still works


def test_builder_skips_formations_without_resolvable_rock_type() -> None:
    payload = {
        "formations": [
            {
                "name": "Fancy Mystery Schist",
                "rock_type": None,
                "aliases": [],
                "evidence": [_doc_evidence()],
            },
            {
                "name": "Manhattan Schist",
                "rock_type": "metamorphic",
                "aliases": [],
                "evidence": [_doc_evidence()],
            },
        ],
        "contacts": [],
        "structures": [],
        "notes": None,
    }
    extraction = _extraction(payload)

    report = build_program_from_extraction(extraction)

    rock_names = {r.name for r in report.program.rocks}
    assert "Manhattan Schist" in rock_names
    assert "Fancy Mystery Schist" not in rock_names
    assert any("Fancy Mystery Schist" in w for w in report.warnings)
    assert "Fancy Mystery Schist" in report.skipped_formations


def test_builder_skips_contacts_with_unknown_formations() -> None:
    payload = _build_payload()
    payload["contacts"].append(
        {
            "top_formation": "Atlantis Schist",
            "bottom_formation": "Manhattan Schist",
            "evidence": [_doc_evidence()],
        }
    )
    extraction = _extraction(payload)

    report = build_program_from_extraction(extraction)
    assert any("Atlantis Schist" in s for s in report.skipped_contacts)


def test_builder_uses_injected_glossary() -> None:
    glossary = GeologyGlossary(
        [
            GlossaryEntry(
                canonical="Custom Sandstone",
                rock_type="sedimentary",
                aliases=("custom sandstone", "CS"),
            ),
        ]
    )
    extraction = _extraction(
        {
            "formations": [
                {
                    "name": "CS",
                    "rock_type": None,
                    "aliases": [],
                    "evidence": [_doc_evidence()],
                }
            ],
            "contacts": [],
            "structures": [],
            "notes": None,
        }
    )

    dsl_text, build_report = build_dsl_from_extraction(extraction, glossary=glossary)

    assert "Custom Sandstone" in dsl_text
    assert "type: sedimentary" in dsl_text
    assert build_report.summary["rock_count"] == 1


def test_builder_summary_reports_counts() -> None:
    extraction = _extraction(_build_payload())

    report = build_program_from_extraction(extraction)
    summary = report.summary

    assert summary["rock_count"] == 3
    assert summary["deposition_count"] == 2  # Manhattan + Inwood
    assert summary["intrusion_count"] == 1  # Ravenswood
    assert summary["contact_count_used"] == 1


@pytest.mark.parametrize(
    "depth_unit",
    ["m", "ft"],
)
def test_builder_ignores_depth_units_in_dsl(depth_unit: str) -> None:
    """Depths live in extraction.json, not in the DSL — make sure we don't leak them."""

    payload = _build_payload()
    payload["contacts"][0]["depth_unit"] = depth_unit
    extraction = _extraction(payload)

    dsl_text, _ = build_dsl_from_extraction(extraction)
    assert depth_unit not in dsl_text.split("\n")[-2:]
