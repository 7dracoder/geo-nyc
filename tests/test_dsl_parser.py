"""DSL parser & serializer tests.

Round-trip fidelity guarantees that anything we accept as DSL input we
can also re-emit deterministically — important for run artefacts.
"""

from __future__ import annotations

import pytest

from geo_nyc.parsers.dsl import (
    AbsoluteTime,
    DepositionEvent,
    EpochTime,
    ErosionEvent,
    IntrusionEvent,
    IntrusionStyle,
    RockType,
    TimeUnit,
    parse,
    serialize,
)
from geo_nyc.parsers.dsl.errors import DSLSyntaxError

SIMPLE_DSL = """
ROCK R1 [ name: "Manhattan Schist"; type: metamorphic; age: 450Ma ]
ROCK R2 [ name: "Glacial Till"; type: sedimentary ]

DEPOSITION D1 [ rock: R1; time: 450Ma ]
EROSION E1 [ after: D1 ]
DEPOSITION D2 [ rock: R2; after: E1 ]
"""


def test_parse_simple_program() -> None:
    program = parse(SIMPLE_DSL)
    assert len(program.rocks) == 2
    assert {r.id for r in program.rocks} == {"R1", "R2"}

    schist = next(r for r in program.rocks if r.id == "R1")
    assert schist.name == "Manhattan Schist"
    assert schist.rock_type is RockType.METAMORPHIC
    assert isinstance(schist.age, AbsoluteTime)
    assert schist.age.value == 450
    assert schist.age.unit is TimeUnit.MA

    assert len(program.depositions) == 2
    assert len(program.erosions) == 1
    assert program.erosions[0].after == ("D1",)


def test_round_trip_is_idempotent() -> None:
    program1 = parse(SIMPLE_DSL)
    text1 = serialize(program1)
    program2 = parse(text1)
    text2 = serialize(program2)
    assert text1 == text2

    # Compare the AST stripped of source locations.
    def normalize(program):
        return tuple(
            tuple(
                getattr(node, attr)
                for attr in node.__dataclass_fields__
                if attr != "location"
            )
            for node in program.walk()
        )

    assert normalize(program1) == normalize(program2)


def test_intrusion_with_style_and_after() -> None:
    text = """
    ROCK R1 [ name: "Granite"; type: intrusive; age: 100Ma ]
    DEPOSITION D1 [ rock: R1; time: 200Ma ]
    INTRUSION I1 [ rock: R1; style: stock; after: D1 ]
    """
    program = parse(text)
    intr = program.intrusions[0]
    assert isinstance(intr, IntrusionEvent)
    assert intr.style is IntrusionStyle.STOCK
    assert intr.after == ("D1",)


def test_unknown_age_and_epoch() -> None:
    text = """
    ROCK R1 [ name: "Mystery"; type: sedimentary; age: "?" ]
    ROCK R2 [ name: "Eocene Shale"; type: sedimentary; age: late Eocene ]
    DEPOSITION D1 [ rock: R1 ]
    DEPOSITION D2 [ rock: R2 ]
    """
    program = parse(text)
    rock_by_id = {r.id: r for r in program.rocks}
    from geo_nyc.parsers.dsl import UnknownTime  # local to keep top scope tidy

    assert isinstance(rock_by_id["R1"].age, UnknownTime)
    assert isinstance(rock_by_id["R2"].age, EpochTime)
    assert rock_by_id["R2"].age.epoch_name.strip() == "late Eocene"


def test_syntax_error_reports_location() -> None:
    # Missing the opening bracket; this is unambiguously invalid.
    bad = 'ROCK R1 name: "x"; type: sedimentary ]'
    with pytest.raises(DSLSyntaxError) as info:
        parse(bad)
    assert info.value.line == 1
    assert info.value.column > 0


def test_missing_close_bracket_raises_syntax_error() -> None:
    with pytest.raises(DSLSyntaxError):
        parse('ROCK R1 [ name: "Bedrock"; type: metamorphic')


def test_empty_program_is_valid() -> None:
    program = parse("")
    assert program.rocks == ()
    assert program.depositions == ()
    assert program.erosions == ()
    assert program.intrusions == ()
    assert isinstance(program.depositions, tuple)


def test_event_collections_have_correct_types() -> None:
    text = """
    ROCK R1 [ name: "S"; type: sedimentary ]
    DEPOSITION D1 [ rock: R1 ]
    EROSION E1 [ after: D1 ]
    INTRUSION I1 [ rock: R1; after: E1 ]
    """
    program = parse(text)
    assert all(isinstance(d, DepositionEvent) for d in program.depositions)
    assert all(isinstance(e, ErosionEvent) for e in program.erosions)
    assert all(isinstance(i, IntrusionEvent) for i in program.intrusions)
