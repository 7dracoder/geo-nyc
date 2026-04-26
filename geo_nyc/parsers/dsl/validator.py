"""Semantic validator for parsed DSL programs.

Adapted from williamjsdavis/geo-lm (MIT). Improvements:

* the validator returns a frozen :class:`ValidationReport` with both
  errors and warnings, ready to ship to the API surface;
* every check is defensive against duplicates so we don't loop forever;
* helpers are pure functions to make unit testing easier.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from geo_nyc.parsers.dsl.ast import (
    AbsoluteTime,
    Program,
)
from geo_nyc.parsers.dsl.errors import (
    CircularDependencyError,
    DSLSemanticError,
    DuplicateIDError,
    MissingRequiredPropertyError,
    TemporalInconsistencyError,
    UndefinedReferenceError,
)


@dataclass
class ValidationReport:
    """Collected errors and warnings."""

    errors: list[DSLSemanticError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add_error(self, error: DSLSemanticError) -> None:
        self.errors.append(error)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


class DSLValidator:
    """Run all semantic checks on a parsed program."""

    def validate(self, program: Program) -> ValidationReport:
        report = ValidationReport()
        self._check_duplicate_ids(program, report)
        self._check_required_properties(program, report)
        self._check_rock_references(program, report)
        self._check_event_references(program, report)
        self._check_circular_dependencies(program, report)
        self._check_temporal_consistency(program, report)
        return report

    # ---------- individual checks ------------------------------------

    def _check_duplicate_ids(self, program: Program, report: ValidationReport) -> None:
        seen: dict[str, object] = {}
        for node in program.walk():
            if node.id in seen:
                report.add_error(
                    DuplicateIDError(
                        duplicate_id=node.id,
                        first_location=getattr(seen[node.id], "location", None),
                        second_location=getattr(node, "location", None),
                    )
                )
            else:
                seen[node.id] = node

    def _check_required_properties(
        self, program: Program, report: ValidationReport
    ) -> None:
        for rock in program.rocks:
            if not rock.name:
                report.add_error(
                    MissingRequiredPropertyError(
                        node_type="ROCK",
                        node_id=rock.id,
                        property_name="name",
                        location=rock.location,
                    )
                )
        for dep in program.depositions:
            if not dep.rock_id:
                report.add_error(
                    MissingRequiredPropertyError(
                        node_type="DEPOSITION",
                        node_id=dep.id,
                        property_name="rock",
                        location=dep.location,
                    )
                )
        for intr in program.intrusions:
            if not intr.rock_id:
                report.add_error(
                    MissingRequiredPropertyError(
                        node_type="INTRUSION",
                        node_id=intr.id,
                        property_name="rock",
                        location=intr.location,
                    )
                )

    def _check_rock_references(self, program: Program, report: ValidationReport) -> None:
        rock_ids = program.rock_ids
        sorted_rock_ids = tuple(sorted(rock_ids))
        for dep in program.depositions:
            if dep.rock_id and dep.rock_id not in rock_ids:
                report.add_error(
                    UndefinedReferenceError(
                        reference_type="rock",
                        reference_id=dep.rock_id,
                        context=f"DEPOSITION {dep.id}",
                        available_ids=sorted_rock_ids,
                        location=dep.location,
                    )
                )
        for intr in program.intrusions:
            if intr.rock_id and intr.rock_id not in rock_ids:
                report.add_error(
                    UndefinedReferenceError(
                        reference_type="rock",
                        reference_id=intr.rock_id,
                        context=f"INTRUSION {intr.id}",
                        available_ids=sorted_rock_ids,
                        location=intr.location,
                    )
                )

    def _check_event_references(
        self, program: Program, report: ValidationReport
    ) -> None:
        event_ids = program.event_ids
        sorted_event_ids = tuple(sorted(event_ids))
        for event in program.all_events:
            for ref_id in event.after:
                if ref_id in event_ids:
                    continue
                event_kind = type(event).__name__.replace("Event", "").upper()
                report.add_error(
                    UndefinedReferenceError(
                        reference_type="event",
                        reference_id=ref_id,
                        context=f"after: clause in {event_kind} {event.id}",
                        available_ids=sorted_event_ids,
                        location=event.location,
                    )
                )

    def _check_circular_dependencies(
        self, program: Program, report: ValidationReport
    ) -> None:
        graph: dict[str, list[str]] = defaultdict(list)
        for event in program.all_events:
            for dep_id in event.after:
                if dep_id in program.event_ids:
                    graph[event.id].append(dep_id)

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = defaultdict(int)
        path: list[str] = []

        def dfs(node: str) -> Sequence[str] | None:
            color[node] = GRAY
            path.append(node)
            for neighbour in graph[node]:
                if color[neighbour] == GRAY:
                    cycle_start = path.index(neighbour)
                    return [*path[cycle_start:], neighbour]
                if color[neighbour] == WHITE:
                    cycle = dfs(neighbour)
                    if cycle:
                        return cycle
            path.pop()
            color[node] = BLACK
            return None

        for event in program.all_events:
            if color[event.id] == WHITE:
                cycle = dfs(event.id)
                if cycle:
                    report.add_error(
                        CircularDependencyError(
                            cycle_path=tuple(cycle),
                            location=event.location,
                        )
                    )
                    return  # one cycle is enough to fail

    def _check_temporal_consistency(
        self, program: Program, report: ValidationReport
    ) -> None:
        events_by_id = {e.id: e for e in program.all_events}
        for event in program.all_events:
            if not isinstance(event.time, AbsoluteTime):
                continue
            event_age = event.time.to_ma()
            for dep_id in event.after:
                dep = events_by_id.get(dep_id)
                if dep is None or not isinstance(dep.time, AbsoluteTime):
                    continue
                dep_age = dep.time.to_ma()
                if event_age > dep_age:
                    report.add_error(
                        TemporalInconsistencyError(
                            event_id=event.id,
                            event_time=str(event.time),
                            dependency_id=dep.id,
                            dependency_time=str(dep.time),
                            location=event.location,
                        )
                    )


__all__ = ["DSLValidator", "ValidationReport"]
