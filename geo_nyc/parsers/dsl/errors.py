"""DSL parser/validator error classes.

Adapted from williamjsdavis/geo-lm (MIT). We unify the error hierarchy
so callers can catch a single ``DSLError`` while still using
:class:`isinstance` to distinguish syntax from semantic problems.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from geo_nyc.exceptions import (
    DSLError as _BaseDSLError,
)
from geo_nyc.exceptions import (
    DSLSyntaxError as _BaseSyntaxError,
)
from geo_nyc.exceptions import (
    DSLValidationError as _BaseValidationError,
)
from geo_nyc.parsers.dsl.ast import SourceLocation


class DSLError(_BaseDSLError):
    """Base for all DSL errors. Subclasses :class:`geo_nyc.exceptions.DSLError`."""

    def __init__(self, message: str, location: SourceLocation | None = None) -> None:
        self.location = location
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.location is not None:
            return f"{self.location}: {self.args[0]}"
        return str(self.args[0])


class DSLParseError(DSLError):
    """Generic parse failure not classified as syntax."""


class DSLSyntaxError(_BaseSyntaxError, DSLError):
    """Lark syntax error, decorated with the offending source line."""

    def __init__(
        self,
        message: str,
        line: int,
        column: int,
        context_line: str = "",
        expected: Sequence[str] = (),
    ) -> None:
        self.line = line
        self.column = column
        self.context_line = context_line
        self.expected = list(expected)

        formatted = self._format_message(message)
        DSLError.__init__(
            self,
            formatted,
            location=SourceLocation(line=line, column=column),
        )

    def _format_message(self, message: str) -> str:
        out = [f"Syntax error at line {self.line}, column {self.column}: {message}"]
        if self.context_line:
            out.append(f"  {self.context_line}")
            out.append("  " + " " * max(self.column - 1, 0) + "^")
        if self.expected:
            shown = ", ".join(sorted(self.expected)[:5])
            if len(self.expected) > 5:
                shown += f", … ({len(self.expected)} options)"
            out.append(f"  Expected: {shown}")
        return "\n".join(out)

    @classmethod
    def from_lark_error(cls, error: object, source: str) -> DSLSyntaxError:
        from lark.exceptions import UnexpectedCharacters, UnexpectedToken  # local import

        line = getattr(error, "line", 1)
        column = getattr(error, "column", 1)
        lines = source.splitlines()
        context_line = lines[line - 1] if 0 < line <= len(lines) else ""

        expected: list[str] = []
        if isinstance(error, UnexpectedToken):
            token = getattr(error, "token", "")
            expected = list(getattr(error, "expected", []) or [])
            message = f"Unexpected token '{token}'"
        elif isinstance(error, UnexpectedCharacters):
            message = f"Unexpected character '{getattr(error, 'char', '?')}'"
        else:
            message = str(error)

        return cls(
            message=message,
            line=line,
            column=column,
            context_line=context_line,
            expected=expected,
        )


class DSLSemanticError(_BaseValidationError, DSLError):
    """Base for semantic / validation failures."""


@dataclass
class UndefinedReferenceError(DSLSemanticError):
    reference_type: str
    reference_id: str
    context: str
    available_ids: tuple[str, ...] = ()
    location: SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:  # pragma: no cover - exercised in tests
        DSLError.__init__(self, self._build_message(), self.location)

    def _build_message(self) -> str:
        msg = (
            f"Undefined {self.reference_type} '{self.reference_id}' in {self.context}"
        )
        if self.available_ids:
            suggestions = self._suggest()
            if suggestions:
                msg += f". Did you mean: {', '.join(suggestions)}?"
            else:
                msg += f". Available: {', '.join(self.available_ids[:5])}"
        return msg

    def _suggest(self) -> list[str]:
        def edit_distance(a: str, b: str) -> int:
            if len(a) < len(b):
                a, b = b, a
            if not b:
                return len(a)
            prev = list(range(len(b) + 1))
            for i, ca in enumerate(a):
                curr = [i + 1]
                for j, cb in enumerate(b):
                    curr.append(
                        min(
                            prev[j + 1] + 1,
                            curr[j] + 1,
                            prev[j] + (ca != cb),
                        )
                    )
                prev = curr
            return prev[-1]

        scored = [(edit_distance(self.reference_id, aid), aid) for aid in self.available_ids]
        scored.sort()
        return [aid for dist, aid in scored[:3] if dist <= 2]


@dataclass
class DuplicateIDError(DSLSemanticError):
    duplicate_id: str
    first_location: SourceLocation | None = None
    second_location: SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:  # pragma: no cover
        msg = f"Duplicate ID '{self.duplicate_id}'"
        if self.first_location is not None:
            msg += f" (first defined at {self.first_location})"
        DSLError.__init__(self, msg, self.second_location)


@dataclass
class CircularDependencyError(DSLSemanticError):
    cycle_path: tuple[str, ...]
    location: SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:  # pragma: no cover
        DSLError.__init__(
            self,
            f"Circular dependency detected: {' -> '.join(self.cycle_path)}",
            self.location,
        )


@dataclass
class MissingRequiredPropertyError(DSLSemanticError):
    node_type: str
    node_id: str
    property_name: str
    location: SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:  # pragma: no cover
        DSLError.__init__(
            self,
            (
                f"{self.node_type} '{self.node_id}' is missing required "
                f"property '{self.property_name}'"
            ),
            self.location,
        )


@dataclass
class TemporalInconsistencyError(DSLSemanticError):
    event_id: str
    event_time: str
    dependency_id: str
    dependency_time: str
    location: SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:  # pragma: no cover
        DSLError.__init__(
            self,
            (
                f"Temporal inconsistency: {self.event_id} ({self.event_time}) is "
                f"declared after {self.dependency_id} ({self.dependency_time}), "
                f"but {self.event_time} is older"
            ),
            self.location,
        )


__all__ = [
    "CircularDependencyError",
    "DSLError",
    "DSLParseError",
    "DSLSemanticError",
    "DSLSyntaxError",
    "DuplicateIDError",
    "MissingRequiredPropertyError",
    "TemporalInconsistencyError",
    "UndefinedReferenceError",
]
