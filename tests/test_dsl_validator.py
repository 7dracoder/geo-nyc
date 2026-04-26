"""DSL semantic validator tests."""

from __future__ import annotations

from geo_nyc.parsers.dsl import parse, validate
from geo_nyc.parsers.dsl.errors import (
    CircularDependencyError,
    DuplicateIDError,
    MissingRequiredPropertyError,
    TemporalInconsistencyError,
    UndefinedReferenceError,
)


def test_valid_program() -> None:
    text = """
    ROCK R1 [ name: "Schist"; type: metamorphic; age: 450Ma ]
    ROCK R2 [ name: "Till"; type: sedimentary ]
    DEPOSITION D1 [ rock: R1; time: 450Ma ]
    EROSION E1 [ after: D1 ]
    DEPOSITION D2 [ rock: R2; after: E1 ]
    """
    report = validate(parse(text))
    assert report.is_valid, report.errors


def test_undefined_rock_reference() -> None:
    text = """
    ROCK R1 [ name: "Schist"; type: metamorphic ]
    DEPOSITION D1 [ rock: R_BOGUS ]
    """
    report = validate(parse(text))
    assert not report.is_valid
    assert any(isinstance(e, UndefinedReferenceError) for e in report.errors)


def test_undefined_event_reference() -> None:
    text = """
    ROCK R1 [ name: "S"; type: sedimentary ]
    DEPOSITION D1 [ rock: R1; after: D_NOPE ]
    """
    report = validate(parse(text))
    assert any(isinstance(e, UndefinedReferenceError) for e in report.errors)


def test_duplicate_id() -> None:
    text = """
    ROCK R1 [ name: "S"; type: sedimentary ]
    ROCK R1 [ name: "Q"; type: volcanic ]
    """
    report = validate(parse(text))
    assert any(isinstance(e, DuplicateIDError) for e in report.errors)


def test_missing_required_property() -> None:
    text = """
    ROCK R1 [ name: "S"; type: sedimentary ]
    DEPOSITION D1 [ ]
    """
    report = validate(parse(text))
    assert any(isinstance(e, MissingRequiredPropertyError) for e in report.errors)


def test_circular_dependency() -> None:
    text = """
    ROCK R1 [ name: "S"; type: sedimentary ]
    DEPOSITION D1 [ rock: R1; after: D2 ]
    DEPOSITION D2 [ rock: R1; after: D1 ]
    """
    report = validate(parse(text))
    assert any(isinstance(e, CircularDependencyError) for e in report.errors)


def test_temporal_inconsistency() -> None:
    text = """
    ROCK R1 [ name: "S"; type: sedimentary ]
    DEPOSITION D1 [ rock: R1; time: 100Ma ]
    DEPOSITION D2 [ rock: R1; time: 200Ma; after: D1 ]
    """
    report = validate(parse(text))
    assert any(isinstance(e, TemporalInconsistencyError) for e in report.errors)
