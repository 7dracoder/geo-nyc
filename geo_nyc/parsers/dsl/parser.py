"""Lark-based DSL parser.

Adapted from williamjsdavis/geo-lm (MIT). The grammar is loaded once at
module import time so subsequent calls are cheap; the parser instance
is thread-safe for read-only ``parse`` calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lark import Lark, Transformer, v_args
from lark.exceptions import LarkError, UnexpectedInput

from geo_nyc.parsers.dsl.ast import (
    AbsoluteTime,
    DepositionEvent,
    EpochTime,
    ErosionEvent,
    IntrusionEvent,
    IntrusionStyle,
    Program,
    RockDefinition,
    RockType,
    SourceLocation,
    TimeUnit,
    UnknownTime,
)
from geo_nyc.parsers.dsl.errors import DSLParseError, DSLSyntaxError

GRAMMAR_PATH = Path(__file__).resolve().parent / "grammar.lark"


def _location_from_meta(meta: Any) -> SourceLocation | None:
    if meta is None or not hasattr(meta, "line"):
        return None
    return SourceLocation(
        line=meta.line,
        column=meta.column,
        end_line=getattr(meta, "end_line", None),
        end_column=getattr(meta, "end_column", None),
    )


class _ASTTransformer(Transformer):
    """Lark visitor that materialises tree nodes into our dataclasses."""

    # --- top-level --------------------------------------------------------

    def start(self, items: list[Any]) -> Program:
        rocks: list[RockDefinition] = []
        depositions: list[DepositionEvent] = []
        erosions: list[ErosionEvent] = []
        intrusions: list[IntrusionEvent] = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, RockDefinition):
                rocks.append(item)
            elif isinstance(item, DepositionEvent):
                depositions.append(item)
            elif isinstance(item, ErosionEvent):
                erosions.append(item)
            elif isinstance(item, IntrusionEvent):
                intrusions.append(item)
        return Program(
            rocks=tuple(rocks),
            depositions=tuple(depositions),
            erosions=tuple(erosions),
            intrusions=tuple(intrusions),
        )

    def statement(self, items: list[Any]) -> Any:
        return items[0] if items else None

    # --- ROCK -------------------------------------------------------------

    @v_args(meta=True)
    def rock_stmt(self, meta: Any, items: list[Any]) -> RockDefinition:
        id_token = str(items[0])
        props: dict[str, Any] = items[1] if len(items) > 1 else {}
        return RockDefinition(
            id=id_token,
            name=props.get("name", ""),
            rock_type=props.get("type", RockType.SEDIMENTARY),
            age=props.get("age"),
            location=_location_from_meta(meta),
        )

    def rock_body(self, items: list[Any]) -> dict[str, Any]:
        return self._collect_props(items)

    def name_prop(self, items: list[Any]) -> tuple[str, str]:
        # The STRING terminal admits both ``"quoted"`` and bare unquoted
        # forms. The unquoted alternative greedily includes any leading
        # whitespace, so we always trim *first* and then strip surrounding
        # quotes if both are present.
        raw = str(items[0]).strip()
        if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        return ("name", raw.strip())

    def type_prop(self, items: list[Any]) -> tuple[str, RockType]:
        return ("type", RockType(str(items[0])))

    def age_prop(self, items: list[Any]) -> tuple[str, Any]:
        return ("age", items[0])

    # --- events -----------------------------------------------------------

    @v_args(meta=True)
    def deposition_stmt(self, meta: Any, items: list[Any]) -> DepositionEvent:
        id_token = str(items[0])
        props: dict[str, Any] = items[1] if len(items) > 1 else {}
        return DepositionEvent(
            id=id_token,
            rock_id=props.get("rock", ""),
            time=props.get("time"),
            after=tuple(props.get("after", ())),
            location=_location_from_meta(meta),
        )

    @v_args(meta=True)
    def erosion_stmt(self, meta: Any, items: list[Any]) -> ErosionEvent:
        id_token = str(items[0])
        props: dict[str, Any] = items[1] if len(items) > 1 else {}
        return ErosionEvent(
            id=id_token,
            time=props.get("time"),
            after=tuple(props.get("after", ())),
            location=_location_from_meta(meta),
        )

    @v_args(meta=True)
    def intrusion_stmt(self, meta: Any, items: list[Any]) -> IntrusionEvent:
        id_token = str(items[0])
        props: dict[str, Any] = items[1] if len(items) > 1 else {}
        return IntrusionEvent(
            id=id_token,
            rock_id=props.get("rock", ""),
            style=props.get("style"),
            time=props.get("time"),
            after=tuple(props.get("after", ())),
            location=_location_from_meta(meta),
        )

    def event_body(self, items: list[Any]) -> dict[str, Any]:
        return self._collect_props(items)

    def erosion_body(self, items: list[Any]) -> dict[str, Any]:
        return self._collect_props(items)

    def intrusion_body(self, items: list[Any]) -> dict[str, Any]:
        return self._collect_props(items)

    def rock_ref_prop(self, items: list[Any]) -> tuple[str, str]:
        return ("rock", str(items[0]))

    def time_prop(self, items: list[Any]) -> tuple[str, Any]:
        return ("time", items[0])

    def after_prop(self, items: list[Any]) -> tuple[str, list[str]]:
        return ("after", items[0])

    def style_prop(self, items: list[Any]) -> tuple[str, IntrusionStyle]:
        return ("style", IntrusionStyle(str(items[0])))

    # --- time values ------------------------------------------------------

    def absolute_age(self, items: list[Any]) -> AbsoluteTime:
        return self._absolute_time(items)

    def absolute_time(self, items: list[Any]) -> AbsoluteTime:
        return self._absolute_time(items)

    def epoch_age(self, items: list[Any]) -> EpochTime:
        return EpochTime(epoch_name=str(items[0]).strip())

    def epoch_time(self, items: list[Any]) -> EpochTime:
        return EpochTime(epoch_name=str(items[0]).strip())

    def unknown_age(self, items: list[Any]) -> UnknownTime:
        return UnknownTime()

    def unknown_time(self, items: list[Any]) -> UnknownTime:
        return UnknownTime()

    def id_list(self, items: list[Any]) -> list[str]:
        return [str(item) for item in items]

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _collect_props(items: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in items:
            if item is None:
                continue
            if isinstance(item, tuple) and len(item) == 2:
                result[item[0]] = item[1]
        return result

    @staticmethod
    def _absolute_time(items: list[Any]) -> AbsoluteTime:
        return AbsoluteTime(value=float(items[0]), unit=TimeUnit(str(items[1])))


class GeologyDSLParser:
    """Re-usable parser. Construct once, reuse for many parses."""

    def __init__(self, grammar_path: Path | None = None) -> None:
        path = grammar_path or GRAMMAR_PATH
        with open(path, encoding="utf-8") as fh:
            grammar_text = fh.read()
        self._lark = Lark(
            grammar_text,
            start="start",
            parser="earley",
            propagate_positions=True,
            maybe_placeholders=False,
        )
        self._transformer = _ASTTransformer()

    def parse(self, text: str) -> Program:
        try:
            tree = self._lark.parse(text)
        except UnexpectedInput as exc:
            raise DSLSyntaxError.from_lark_error(exc, text) from exc
        except LarkError as exc:
            raise DSLParseError(str(exc)) from exc
        return self._transformer.transform(tree)


__all__ = ["GRAMMAR_PATH", "GeologyDSLParser"]
