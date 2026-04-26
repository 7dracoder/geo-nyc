"""Serialise a :class:`Program` back into DSL source.

Adapted from williamjsdavis/geo-lm (MIT). Output is deterministic and
round-trip-safe, which is critical for snapshot tests and for storing
the canonical DSL alongside generated artefacts.
"""

from __future__ import annotations

from collections.abc import Sequence
from io import StringIO

from geo_nyc.parsers.dsl.ast import (
    AbsoluteTime,
    DepositionEvent,
    EpochTime,
    ErosionEvent,
    IntrusionEvent,
    Program,
    RockDefinition,
    TimeValue,
    UnknownTime,
)


class DSLSerializer:
    """Serialise an AST :class:`Program` back to DSL text."""

    def serialize(self, program: Program) -> str:
        out = StringIO()
        self._write_program(program, out)
        return out.getvalue()

    # ---------- helpers ------------------------------------------------

    def _write_program(self, program: Program, out: StringIO) -> None:
        for rock in program.rocks:
            self._write_rock(rock, out)

        if program.rocks and (program.depositions or program.erosions or program.intrusions):
            out.write("\n")

        for dep in program.depositions:
            self._write_deposition(dep, out)
        for ero in program.erosions:
            self._write_erosion(ero, out)
        for intr in program.intrusions:
            self._write_intrusion(intr, out)

    def _write_rock(self, rock: RockDefinition, out: StringIO) -> None:
        props = [f'name: "{rock.name}"', f"type: {rock.rock_type.value}"]
        if rock.age is not None:
            props.append(f"age: {self._fmt_time(rock.age)}")
        self._write_statement("ROCK", rock.id, props, out)

    def _write_deposition(self, dep: DepositionEvent, out: StringIO) -> None:
        props = [f"rock: {dep.rock_id}"]
        if dep.time is not None:
            props.append(f"time: {self._fmt_time(dep.time)}")
        if dep.after:
            props.append(f"after: {self._fmt_id_list(dep.after)}")
        self._write_statement("DEPOSITION", dep.id, props, out)

    def _write_erosion(self, ero: ErosionEvent, out: StringIO) -> None:
        props: list[str] = []
        if ero.time is not None:
            props.append(f"time: {self._fmt_time(ero.time)}")
        if ero.after:
            props.append(f"after: {self._fmt_id_list(ero.after)}")
        self._write_statement("EROSION", ero.id, props, out)

    def _write_intrusion(self, intr: IntrusionEvent, out: StringIO) -> None:
        props = [f"rock: {intr.rock_id}"]
        if intr.style is not None:
            props.append(f"style: {intr.style.value}")
        if intr.time is not None:
            props.append(f"time: {self._fmt_time(intr.time)}")
        if intr.after:
            props.append(f"after: {self._fmt_id_list(intr.after)}")
        self._write_statement("INTRUSION", intr.id, props, out)

    @staticmethod
    def _fmt_time(value: TimeValue) -> str:
        if isinstance(value, AbsoluteTime):
            unit = value.unit.value
            if value.value == int(value.value):
                return f"{int(value.value)}{unit}"
            return f"{value.value}{unit}"
        if isinstance(value, EpochTime):
            return value.epoch_name
        if isinstance(value, UnknownTime):
            return '"?"'
        # Defensive: future TimeValue subclasses should be added here.
        return str(value)  # pragma: no cover

    @staticmethod
    def _fmt_id_list(ids: Sequence[str]) -> str:
        return ", ".join(ids)

    @staticmethod
    def _write_statement(keyword: str, identifier: str, props: list[str], out: StringIO) -> None:
        body = "; ".join(props)
        out.write(f"{keyword} {identifier} [ {body} ]\n")


__all__ = ["DSLSerializer"]
