"""Build a DSL :class:`Program` from a structured :class:`LLMExtraction`.

The structured-extraction → DSL translation is deterministic and pure
Python; the LLM is only responsible for facts, not grammar. We:

1. Normalise every formation name through the NYC glossary, dedup by
   canonical spelling.
2. Generate stable identifiers (``R_MANHATTAN_SCHIST``,
   ``D_MANHATTAN_SCHIST``) so re-runs over the same extraction produce
   byte-identical DSL.
3. Topologically order formations using contact relationships
   (``top_formation`` overlies ``bottom_formation`` ⇒ bottom must be
   deposited *before* top).
4. Emit one ``ROCK`` statement per canonical formation.
5. Emit a ``DEPOSITION`` (or ``INTRUSION`` for intrusive rocks) per
   formation, chained via ``after:`` references in stratigraphic order.

If the contact graph contains a cycle we fall back to the order in which
the LLM mentioned the formations and record a warning, so the DSL we
emit always serialises and parses successfully.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from geo_nyc.domain.normalization import GeologyGlossary, default_glossary
from geo_nyc.extraction.structured import (
    Contact,
    Formation,
    LLMExtraction,
)
from geo_nyc.extraction.structured import (
    RockType as StructuredRockType,
)
from geo_nyc.parsers.dsl.ast import (
    DepositionEvent,
    IntrusionEvent,
    Program,
    RockDefinition,
    RockType,
)
from geo_nyc.parsers.dsl.serializer import DSLSerializer

_NON_WORD_ID = re.compile(r"[^A-Za-z0-9]+")
_MAX_ID_BODY_LEN = 24


@dataclass(slots=True)
class DSLBuildReport:
    """Side-channel diagnostics from :func:`build_program_from_extraction`."""

    program: Program
    rock_id_by_formation: dict[str, str] = field(default_factory=dict)
    deposition_id_by_formation: dict[str, str] = field(default_factory=dict)
    canonical_by_input: dict[str, str] = field(default_factory=dict)
    skipped_formations: list[str] = field(default_factory=list)
    skipped_contacts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def build_program_from_extraction(
    extraction: LLMExtraction,
    *,
    glossary: GeologyGlossary | None = None,
) -> DSLBuildReport:
    """Convert ``extraction`` into a serialisable :class:`Program`."""

    glossary = glossary or default_glossary()

    canonical_by_input: dict[str, str] = {}
    canonical_to_formation: dict[str, Formation] = {}
    canonical_order: list[str] = []
    skipped_formations: list[str] = []
    warnings: list[str] = []

    for formation in extraction.formations:
        raw_name = formation.name.strip()
        if not raw_name:
            skipped_formations.append(formation.name)
            continue
        canonical = glossary.canonical(raw_name)
        canonical_by_input[raw_name] = canonical
        for alias in formation.aliases:
            canonical_by_input.setdefault(alias.strip(), canonical)
        if canonical not in canonical_to_formation:
            canonical_to_formation[canonical] = formation
            canonical_order.append(canonical)

    rock_id_by_formation: dict[str, str] = {}
    used_ids: set[str] = set()
    rocks: list[RockDefinition] = []

    for canonical in canonical_order:
        formation = canonical_to_formation[canonical]
        rock_type = _resolve_rock_type(canonical, formation, glossary)
        if rock_type is None:
            warnings.append(
                f"Formation {canonical!r} skipped: rock_type unknown after glossary lookup."
            )
            skipped_formations.append(canonical)
            continue
        rock_id = _allocate_id("R", canonical, used_ids)
        rocks.append(
            RockDefinition(id=rock_id, name=canonical, rock_type=rock_type)
        )
        rock_id_by_formation[canonical] = rock_id

    # Restrict the rest of the pipeline to formations that survived the
    # rock-type filter. ``canonical_order`` keeps mention order so the
    # output is deterministic.
    eligible_order = [c for c in canonical_order if c in rock_id_by_formation]

    # Build the contact graph (bottom -> top means bottom must come first).
    skipped_contacts: list[str] = []
    edges: dict[str, set[str]] = {c: set() for c in eligible_order}
    in_degree: dict[str, int] = {c: 0 for c in eligible_order}
    contacts_kept: list[Contact] = []
    for contact in extraction.contacts:
        top = canonical_by_input.get(contact.top_formation.strip()) or glossary.canonical(
            contact.top_formation
        )
        bottom = canonical_by_input.get(
            contact.bottom_formation.strip()
        ) or glossary.canonical(contact.bottom_formation)
        if top not in rock_id_by_formation or bottom not in rock_id_by_formation:
            skipped_contacts.append(
                f"{contact.top_formation!r} / {contact.bottom_formation!r} "
                f"(at least one side missing from ROCK definitions)"
            )
            continue
        if top == bottom:
            skipped_contacts.append(
                f"{contact.top_formation!r} self-loop ignored."
            )
            continue
        if top in edges[bottom]:
            continue  # already recorded
        edges[bottom].add(top)
        in_degree[top] += 1
        contacts_kept.append(contact)

    ordered = _topological_order(eligible_order, edges, in_degree)
    if ordered is None:
        warnings.append(
            "Contact graph has a cycle; falling back to mention order for the after: chain."
        )
        ordered = list(eligible_order)
        for canonical in ordered:
            in_degree[canonical] = 0
            edges[canonical] = set()

    deposition_id_by_formation: dict[str, str] = {}
    depositions: list[DepositionEvent] = []
    intrusions: list[IntrusionEvent] = []

    previous_event_id: str | None = None
    for canonical in ordered:
        rock_id = rock_id_by_formation[canonical]
        # Always chain to the *previous* event (regardless of contact
        # edges) so we always produce a connected stratigraphic story
        # even when the LLM only logs a partial set of contacts.
        after_ids: tuple[str, ...] = (previous_event_id,) if previous_event_id else ()

        rock_def = next(r for r in rocks if r.id == rock_id)
        if rock_def.rock_type is RockType.INTRUSIVE:
            event_id = _allocate_id("I", canonical, used_ids)
            intrusions.append(
                IntrusionEvent(id=event_id, rock_id=rock_id, after=after_ids)
            )
        else:
            event_id = _allocate_id("D", canonical, used_ids)
            depositions.append(
                DepositionEvent(id=event_id, rock_id=rock_id, after=after_ids)
            )
        deposition_id_by_formation[canonical] = event_id
        previous_event_id = event_id

    program = Program(
        rocks=tuple(rocks),
        depositions=tuple(depositions),
        intrusions=tuple(intrusions),
    )

    summary = {
        "rock_count": len(rocks),
        "deposition_count": len(depositions),
        "intrusion_count": len(intrusions),
        "contact_count_used": len(contacts_kept),
        "contact_count_skipped": len(skipped_contacts),
        "formation_count_skipped": len(skipped_formations),
    }

    return DSLBuildReport(
        program=program,
        rock_id_by_formation=rock_id_by_formation,
        deposition_id_by_formation=deposition_id_by_formation,
        canonical_by_input=canonical_by_input,
        skipped_formations=skipped_formations,
        skipped_contacts=skipped_contacts,
        warnings=warnings,
        summary=summary,
    )


def build_dsl_from_extraction(
    extraction: LLMExtraction,
    *,
    glossary: GeologyGlossary | None = None,
) -> tuple[str, DSLBuildReport]:
    """Convenience: build + serialise."""

    report = build_program_from_extraction(extraction, glossary=glossary)
    text = DSLSerializer().serialize(report.program)
    return text, report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_rock_type(
    canonical: str,
    formation: Formation,
    glossary: GeologyGlossary,
) -> RockType | None:
    """Pick a DSL ``RockType`` for ``formation``, preferring glossary defaults."""

    glossary_type = glossary.rock_type(canonical)
    chosen: StructuredRockType | str | None = (
        glossary_type if glossary_type is not None else formation.rock_type
    )
    if chosen is None:
        return None
    try:
        return RockType(str(chosen))
    except ValueError:
        return None


def _allocate_id(prefix: str, canonical: str, used: set[str]) -> str:
    """Generate a DSL-grammar-compatible identifier from ``canonical``."""

    body = _NON_WORD_ID.sub("_", canonical).strip("_").upper()
    body = body[:_MAX_ID_BODY_LEN] or "X"
    candidate = f"{prefix}_{body}"
    if candidate[0].isdigit():
        candidate = f"{prefix}_X_{body}"
    suffix = 0
    final = candidate
    while final in used:
        suffix += 1
        final = f"{candidate}_{suffix}"
    used.add(final)
    return final


def _topological_order(
    nodes: list[str],
    edges: dict[str, set[str]],
    in_degree: dict[str, int],
) -> list[str] | None:
    """Stable topological sort. Returns ``None`` on cycles.

    Stability matters: re-running the same extraction must produce
    byte-identical DSL. We use the original mention order as the tiebreaker
    rather than ``heapq``-by-name so the output mirrors the source PDF.
    """

    remaining_in_degree = dict(in_degree)
    queue = [n for n in nodes if remaining_in_degree.get(n, 0) == 0]
    ordered: list[str] = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for successor in edges.get(node, ()):
            remaining_in_degree[successor] -= 1
            if remaining_in_degree[successor] == 0:
                queue.append(successor)
    if len(ordered) != len(nodes):
        return None
    return ordered


__all__ = [
    "DSLBuildReport",
    "build_dsl_from_extraction",
    "build_program_from_extraction",
]
