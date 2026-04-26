"""Geology DSL: parser, validator, serializer.

Quick start::

    from geo_nyc.parsers.dsl import parse, validate, serialize

    program = parse(dsl_text)
    report = validate(program)
    if not report.is_valid:
        for error in report.errors:
            print(error)
    print(serialize(program))

The module-level helpers cache parser/validator/serializer instances so
repeated calls don't re-load the grammar from disk.
"""

from __future__ import annotations

from functools import lru_cache

from geo_nyc.parsers.dsl.ast import (
    AbsoluteTime,
    DepositionEvent,
    EpochTime,
    ErosionEvent,
    Event,
    IntrusionEvent,
    IntrusionStyle,
    Program,
    RockDefinition,
    RockType,
    SourceLocation,
    TimeUnit,
    TimeValue,
    UnknownTime,
)
from geo_nyc.parsers.dsl.builder import (
    DSLBuildReport,
    build_dsl_from_extraction,
    build_program_from_extraction,
)
from geo_nyc.parsers.dsl.errors import (
    CircularDependencyError,
    DSLError,
    DSLParseError,
    DSLSemanticError,
    DSLSyntaxError,
    DuplicateIDError,
    MissingRequiredPropertyError,
    TemporalInconsistencyError,
    UndefinedReferenceError,
)
from geo_nyc.parsers.dsl.parser import GeologyDSLParser
from geo_nyc.parsers.dsl.serializer import DSLSerializer
from geo_nyc.parsers.dsl.validator import DSLValidator, ValidationReport


@lru_cache(maxsize=1)
def _parser() -> GeologyDSLParser:
    return GeologyDSLParser()


@lru_cache(maxsize=1)
def _validator() -> DSLValidator:
    return DSLValidator()


@lru_cache(maxsize=1)
def _serializer() -> DSLSerializer:
    return DSLSerializer()


def parse(text: str) -> Program:
    """Parse DSL text into a :class:`Program`."""
    return _parser().parse(text)


def validate(program: Program) -> ValidationReport:
    """Run all semantic checks on a parsed program."""
    return _validator().validate(program)


def serialize(program: Program) -> str:
    """Serialise a program back into canonical DSL text."""
    return _serializer().serialize(program)


def parse_and_validate(text: str) -> tuple[Program, ValidationReport]:
    """Convenience: parse + validate in one call."""
    program = parse(text)
    return program, validate(program)


__all__ = [
    "AbsoluteTime",
    "CircularDependencyError",
    "DSLBuildReport",
    "DSLError",
    "DSLParseError",
    "DSLSemanticError",
    "DSLSerializer",
    "DSLSyntaxError",
    "DSLValidator",
    "DepositionEvent",
    "DuplicateIDError",
    "EpochTime",
    "ErosionEvent",
    "Event",
    "GeologyDSLParser",
    "IntrusionEvent",
    "IntrusionStyle",
    "MissingRequiredPropertyError",
    "Program",
    "RockDefinition",
    "RockType",
    "SourceLocation",
    "TemporalInconsistencyError",
    "TimeUnit",
    "TimeValue",
    "UndefinedReferenceError",
    "UnknownTime",
    "ValidationReport",
    "build_dsl_from_extraction",
    "build_program_from_extraction",
    "parse",
    "parse_and_validate",
    "serialize",
    "validate",
]
