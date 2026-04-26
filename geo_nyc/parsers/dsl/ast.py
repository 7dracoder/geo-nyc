"""AST node definitions for the geology DSL.

Cleaner reimplementation of the upstream geo-lm AST, with:

* explicit ``frozen`` dataclasses everywhere (the upstream version mixed
  frozen and non-frozen variants);
* ``ClassVar``-free public surface so dataclass introspection stays
  simple for serialization;
* a small ``walk`` helper for downstream traversal.

The shape of the AST is otherwise identical so downstream tooling
(GemPy transformer, frontend visualisation) stays compatible.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum


class RockType(StrEnum):
    SEDIMENTARY = "sedimentary"
    VOLCANIC = "volcanic"
    INTRUSIVE = "intrusive"
    METAMORPHIC = "metamorphic"


class IntrusionStyle(StrEnum):
    DIKE = "dike"
    SILL = "sill"
    STOCK = "stock"
    BATHOLITH = "batholith"


class TimeUnit(StrEnum):
    GA = "Ga"
    MA = "Ma"
    KA = "ka"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Position metadata for a node in the original DSL text."""

    line: int
    column: int
    end_line: int | None = None
    end_column: int | None = None

    def __str__(self) -> str:
        if self.end_line is not None and self.end_line != self.line:
            return f"lines {self.line}-{self.end_line}"
        return f"line {self.line}, column {self.column}"


# --- Time values ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AbsoluteTime:
    """An absolute geological time, e.g. ``35Ma``."""

    value: float
    unit: TimeUnit

    def to_ma(self) -> float:
        if self.unit is TimeUnit.GA:
            return self.value * 1_000.0
        if self.unit is TimeUnit.KA:
            return self.value / 1_000.0
        return float(self.value)

    def __str__(self) -> str:
        unit_str = self.unit.value
        if self.value == int(self.value):
            return f"{int(self.value)}{unit_str}"
        return f"{self.value}{unit_str}"


@dataclass(frozen=True, slots=True)
class EpochTime:
    """A geological epoch reference, e.g. ``"late Eocene"``."""

    epoch_name: str

    def __str__(self) -> str:
        return self.epoch_name


@dataclass(frozen=True, slots=True)
class UnknownTime:
    """Placeholder for an unspecified time."""

    def __str__(self) -> str:
        return '"?"'


TimeValue = AbsoluteTime | EpochTime | UnknownTime


# --- Rock + events -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RockDefinition:
    id: str
    name: str
    rock_type: RockType
    age: TimeValue | None = None
    location: SourceLocation | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class DepositionEvent:
    id: str
    rock_id: str
    time: TimeValue | None = None
    after: tuple[str, ...] = ()
    location: SourceLocation | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class ErosionEvent:
    id: str
    time: TimeValue | None = None
    after: tuple[str, ...] = ()
    location: SourceLocation | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class IntrusionEvent:
    id: str
    rock_id: str
    style: IntrusionStyle | None = None
    time: TimeValue | None = None
    after: tuple[str, ...] = ()
    location: SourceLocation | None = field(default=None, compare=False)


Event = DepositionEvent | ErosionEvent | IntrusionEvent


@dataclass(frozen=True, slots=True)
class Program:
    rocks: tuple[RockDefinition, ...] = ()
    depositions: tuple[DepositionEvent, ...] = ()
    erosions: tuple[ErosionEvent, ...] = ()
    intrusions: tuple[IntrusionEvent, ...] = ()

    @property
    def all_events(self) -> tuple[Event, ...]:
        return (*self.depositions, *self.erosions, *self.intrusions)

    @property
    def rock_ids(self) -> set[str]:
        return {r.id for r in self.rocks}

    @property
    def event_ids(self) -> set[str]:
        return {e.id for e in self.all_events}

    def walk(self) -> Iterator[RockDefinition | Event]:
        yield from self.rocks
        yield from self.all_events


__all__ = [
    "AbsoluteTime",
    "DepositionEvent",
    "EpochTime",
    "ErosionEvent",
    "Event",
    "IntrusionEvent",
    "IntrusionStyle",
    "Program",
    "RockDefinition",
    "RockType",
    "SourceLocation",
    "TimeUnit",
    "TimeValue",
    "UnknownTime",
]
