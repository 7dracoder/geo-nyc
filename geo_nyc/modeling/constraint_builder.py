"""Build :class:`GemPyInputs` from a validated DSL ``Program`` + side data.

The builder is the deterministic glue between the symbolic geology
(``Program``) and the numeric world GemPy expects. It never asks the
LLM for help — every value comes from one of three honest sources:

* **extracted** — read directly from a structured ``LLMExtraction``
  (depths from contacts, dip / azimuth from structures);
* **inferred** — synthesised at the AOI center / corners using glossary
  defaults and the program's stratigraphic ordering;
* **fixture** — copied straight out of the demo fixture's
  ``depth_horizons_m`` block.

The result is a fully-populated :class:`GemPyInputs` with at least one
``SurfacePoint`` and one ``Orientation`` per formation, which is the
minimum GemPy needs to compute a coherent stack.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from geo_nyc.domain.normalization import GeologyGlossary, default_glossary
from geo_nyc.extraction.structured import (
    Contact,
    LLMExtraction,
    Structure,
)
from geo_nyc.modeling.constraints import (
    ConstraintSource,
    ExtentBox,
    FormationConstraint,
    GemPyInputs,
    GridResolution3D,
    Orientation,
    SurfacePoint,
)
from geo_nyc.modeling.extent import ModelExtent
from geo_nyc.parsers.dsl.ast import (
    DepositionEvent,
    Event,
    IntrusionEvent,
    Program,
    RockType,
)

# Default dip/azimuth used when the LLM has nothing to say. NYC bedrock
# (Manhattan Schist + friends) sits sub-horizontal at the demo scale, so
# 2° gives GemPy an unambiguous polarity without distorting the model.
_DEFAULT_DIP_DEGREES = 2.0
_DEFAULT_AZIMUTH_DEGREES = 90.0
_FT_TO_M = 0.3048

_FIXTURE_HORIZON_KEYS_BY_NAME: dict[str, str] = {
    # Map canonical formation names (lowercased) → fixture horizon key
    # for the *base* of that formation. Surface-point ``z`` for that
    # formation comes from the *base of the formation above it* — so we
    # treat horizons as interfaces, not formation interiors.
    "anthropogenic fill": "fill_base",
    "fill": "fill_base",
    "glacial outwash": "outwash_base",
    "glacial outwash sand and gravel": "outwash_base",
    "outwash": "outwash_base",
    "glacial till": "till_base",
    "till": "till_base",
    "manhattan schist": "bedrock_top",
    "bedrock": "bedrock_top",
}


@dataclass(slots=True)
class _FormationContext:
    """Per-rock bookkeeping the builder threads through the pipeline."""

    rock_id: str
    name: str
    rock_type: RockType
    canonical: str
    is_intrusive: bool
    stratigraphic_order: int
    color_hex: str | None
    age_ma: float | None
    source: ConstraintSource
    top_z: float
    bottom_z: float


class ConstraintBuilder:
    """Convert a validated DSL ``Program`` into a :class:`GemPyInputs` payload.

    The builder is intentionally pure-functional: same inputs → same
    output. Stochastic-feeling decisions (point spacing, default dips)
    are derived from extent geometry and stratigraphic order, so the
    fixture pipeline is bit-for-bit reproducible across re-runs.
    """

    def __init__(
        self,
        *,
        glossary: GeologyGlossary | None = None,
        grid_resolution: GridResolution3D | None = None,
    ) -> None:
        self._glossary = glossary or default_glossary()
        self._grid_resolution = grid_resolution or GridResolution3D()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build(
        self,
        *,
        program: Program,
        extent: ModelExtent,
        crs: str,
        llm_extraction: LLMExtraction | None = None,
        fixture_extraction: dict[str, Any] | None = None,
        mode_label: str = "fixture",
        run_id: str | None = None,
        document_id: str | None = None,
    ) -> GemPyInputs:
        contexts = self._build_formation_contexts(
            program=program,
            extent=extent,
            llm_extraction=llm_extraction,
            fixture_extraction=fixture_extraction,
        )
        formations = [self._formation_constraint(ctx) for ctx in contexts]

        surface_points = self._build_surface_points(
            contexts=contexts,
            extent=extent,
            llm_extraction=llm_extraction,
            fixture_extraction=fixture_extraction,
        )
        orientations = self._build_orientations(
            contexts=contexts,
            extent=extent,
            llm_extraction=llm_extraction,
        )

        summary = _summarise(
            formations=formations,
            surface_points=surface_points,
            orientations=orientations,
        )
        metadata = {
            "mode": mode_label,
            "run_id": run_id,
            "document_id": document_id,
            "default_dip_degrees": _DEFAULT_DIP_DEGREES,
            "default_azimuth_degrees": _DEFAULT_AZIMUTH_DEGREES,
        }

        return GemPyInputs(
            extent=ExtentBox(**asdict(extent)),
            crs=crs,
            grid_resolution=self._grid_resolution,
            formations=formations,
            surface_points=surface_points,
            orientations=orientations,
            summary=summary,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    # ------------------------------------------------------------------
    # Stratigraphy / context resolution
    # ------------------------------------------------------------------

    def _build_formation_contexts(
        self,
        *,
        program: Program,
        extent: ModelExtent,
        llm_extraction: LLMExtraction | None,
        fixture_extraction: dict[str, Any] | None,
    ) -> list[_FormationContext]:
        if not program.rocks:
            return []

        # Stratigraphic order from the deposition + intrusion chain.
        order = _stratigraphic_order(program)
        rocks_by_id = {r.id: r for r in program.rocks}
        intrusive_rock_ids = {i.rock_id for i in program.intrusions}

        # Resolve a top + bottom depth for every formation. Top z values
        # come from contacts/horizons; the bottom of formation k is the
        # top of formation k+1 (for the deepest one we sink to z_min).
        depth_resolution = _resolve_top_depths(
            program=program,
            order=order,
            extent=extent,
            llm_extraction=llm_extraction,
            fixture_extraction=fixture_extraction,
            glossary=self._glossary,
        )

        contexts: list[_FormationContext] = []
        # ``order`` lists rock ids oldest-first → highest stratigraphic_order.
        # GemPy's stack is "top of column = highest order, bottom = 0",
        # which matches: youngest formations were deposited last and now
        # cap the column.
        for idx, rock_id in enumerate(order):
            rock = rocks_by_id[rock_id]
            top_z, source, _quote = depth_resolution.tops_by_rock.get(
                rock_id, (extent.z_max - (extent.depth * (1 - idx / max(len(order), 1))), "inferred", None)
            )
            bottom_z = depth_resolution.bottoms_by_rock.get(rock_id, extent.z_min)
            contexts.append(
                _FormationContext(
                    rock_id=rock_id,
                    name=rock.name,
                    rock_type=rock.rock_type,
                    canonical=self._glossary.canonical(rock.name),
                    is_intrusive=rock_id in intrusive_rock_ids,
                    stratigraphic_order=idx,
                    color_hex=self._glossary.color_for(rock.name),
                    age_ma=_rock_age_ma(rock),
                    source=source,
                    top_z=top_z,
                    bottom_z=bottom_z,
                )
            )
        return contexts

    def _formation_constraint(self, ctx: _FormationContext) -> FormationConstraint:
        return FormationConstraint(
            rock_id=ctx.rock_id,
            name=ctx.name,
            rock_type=ctx.rock_type.value,
            color_hex=ctx.color_hex,
            stratigraphic_order=ctx.stratigraphic_order,
            is_intrusive=ctx.is_intrusive,
            age_ma=ctx.age_ma,
            source=ctx.source,
            note=(
                "Stratigraphic order derived from deposition/intrusion chain."
                if ctx.source != "extracted"
                else None
            ),
        )

    # ------------------------------------------------------------------
    # Surface points
    # ------------------------------------------------------------------

    def _build_surface_points(
        self,
        *,
        contexts: list[_FormationContext],
        extent: ModelExtent,
        llm_extraction: LLMExtraction | None,
        fixture_extraction: dict[str, Any] | None,
    ) -> list[SurfacePoint]:
        points: list[SurfacePoint] = []
        if not contexts:
            return points

        # 5 placement positions per formation: AOI center + 4 corner
        # offsets at 25% inset (so we don't pile points on the bbox edge,
        # which trips up GemPy's gradient computation).
        positions = _aoi_positions(extent)

        # Pre-compute extracted evidence by canonical formation name so
        # we can attach quotes to inferred points lifted from contacts.
        contact_evidence = _contact_evidence_by_top(llm_extraction, self._glossary)

        for ctx in contexts:
            quote = contact_evidence.get(ctx.canonical)
            # Top-of-formation surface points.
            for label, (px, py) in positions:
                points.append(
                    SurfacePoint(
                        formation_id=ctx.rock_id,
                        x=px,
                        y=py,
                        z=ctx.top_z,
                        source=ctx.source,
                        confidence=0.9 if ctx.source == "extracted" else 0.5,
                        evidence_quote=quote if ctx.source == "extracted" else None,
                        note=(
                            f"Top of {ctx.name} at {label}; "
                            f"depth source = {ctx.source}."
                        ),
                    )
                )
        # Augment with explicit contact points from the LLM extraction
        # (location_text → AOI center is the best we can do without
        # geocoding, but we still flag them as ``extracted`` so the
        # frontend can highlight evidence-backed picks).
        if llm_extraction is not None:
            for explicit in _extracted_contact_points(
                llm_extraction=llm_extraction,
                contexts=contexts,
                extent=extent,
                glossary=self._glossary,
            ):
                points.append(explicit)
        return points

    # ------------------------------------------------------------------
    # Orientations
    # ------------------------------------------------------------------

    def _build_orientations(
        self,
        *,
        contexts: list[_FormationContext],
        extent: ModelExtent,
        llm_extraction: LLMExtraction | None,
    ) -> list[Orientation]:
        orientations: list[Orientation] = []
        if not contexts:
            return orientations

        cx, cy = extent.center_xy
        # Per-formation explicit dips (overrides the default).
        explicit = _explicit_dips_by_canonical(llm_extraction, self._glossary)

        for ctx in contexts:
            entry = explicit.get(ctx.canonical)
            if entry is not None:
                dip_deg, azimuth_deg, evidence = entry
                orientations.append(
                    Orientation(
                        formation_id=ctx.rock_id,
                        x=cx,
                        y=cy,
                        z=ctx.top_z,
                        dip_degrees=dip_deg,
                        azimuth_degrees=azimuth_deg,
                        polarity=1,
                        source="extracted",
                        confidence=0.85,
                        evidence_quote=evidence,
                        note=f"Dip extracted from PDF for {ctx.name}.",
                    )
                )
            else:
                orientations.append(
                    Orientation(
                        formation_id=ctx.rock_id,
                        x=cx,
                        y=cy,
                        z=ctx.top_z,
                        dip_degrees=_DEFAULT_DIP_DEGREES,
                        azimuth_degrees=_DEFAULT_AZIMUTH_DEGREES,
                        polarity=1,
                        source=ctx.source,
                        confidence=0.4,
                        note=(
                            f"Default sub-horizontal anchor for {ctx.name}. "
                            "Replace with extracted dip when one is available."
                        ),
                    )
                )
        return orientations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _DepthResolution:
    tops_by_rock: dict[str, tuple[float, ConstraintSource, str | None]]
    bottoms_by_rock: dict[str, float]


def _stratigraphic_order(program: Program) -> list[str]:
    """Topologically order ``rock_id``s from oldest (deposited first) to youngest.

    The DSL parser already produced a valid program (no cycles), so we
    don't need to re-validate; we just chase the ``after:`` chain.
    """

    events: tuple[Event, ...] = program.all_events
    if not events:
        # No events: fall back to declaration order. Treat first declared
        # rock as oldest so the rest of the pipeline still has *some*
        # ordering signal.
        return [r.id for r in program.rocks]

    rock_ids_with_event: list[str] = []
    seen: set[str] = set()
    in_degree: dict[str, int] = {e.id: 0 for e in events}
    edges: dict[str, list[str]] = defaultdict(list)
    events_by_id = {e.id: e for e in events}
    for event in events:
        for predecessor in event.after:
            if predecessor in events_by_id:
                edges[predecessor].append(event.id)
                in_degree[event.id] += 1

    queue = [eid for eid in (e.id for e in events) if in_degree[eid] == 0]
    while queue:
        eid = queue.pop(0)
        event = events_by_id[eid]
        rock_id = getattr(event, "rock_id", None)
        if rock_id and rock_id not in seen:
            rock_ids_with_event.append(rock_id)
            seen.add(rock_id)
        for successor in edges.get(eid, ()):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    # Append any rocks that had no associated event (e.g. orphan ROCK
    # declarations) at the *end* of the stack so they end up "youngest".
    for rock in program.rocks:
        if rock.id not in seen:
            rock_ids_with_event.append(rock.id)
    return rock_ids_with_event


def _resolve_top_depths(
    *,
    program: Program,
    order: list[str],
    extent: ModelExtent,
    llm_extraction: LLMExtraction | None,
    fixture_extraction: dict[str, Any] | None,
    glossary: GeologyGlossary,
) -> _DepthResolution:
    """Pick a `top_z` (and provenance) for every formation in ``order``."""

    rocks_by_id = {r.id: r for r in program.rocks}
    canonical_by_id = {
        rid: glossary.canonical(rocks_by_id[rid].name) for rid in order
    }

    tops_by_rock: dict[str, tuple[float, ConstraintSource, str | None]] = {}

    # Pass 1: prefer extracted contact depths.
    if llm_extraction is not None:
        for contact in llm_extraction.contacts:
            top_z = _contact_top_z(contact)
            if top_z is None:
                continue
            top_canonical = glossary.canonical(contact.top_formation)
            for rid, canonical in canonical_by_id.items():
                if canonical == top_canonical and rid not in tops_by_rock:
                    tops_by_rock[rid] = (
                        max(extent.z_min, min(top_z, extent.z_max)),
                        "extracted",
                        contact.evidence[0].quote if contact.evidence else None,
                    )
                    break

    # Pass 2: fall back to fixture horizons keyed by canonical name.
    horizons = {}
    if fixture_extraction is not None:
        horizons = (fixture_extraction.get("depth_horizons_m") or {})
    if horizons:
        for rid, canonical in canonical_by_id.items():
            if rid in tops_by_rock:
                continue
            # The *top* of formation X is the *base* of the formation
            # above it in stratigraphy. We need the rock right above
            # X in ``order`` (which is oldest-first so above = next).
            above_top = _top_horizon_for(rid, canonical, order, canonical_by_id, horizons)
            if above_top is not None:
                tops_by_rock[rid] = (
                    max(extent.z_min, min(above_top, extent.z_max)),
                    "fixture",
                    None,
                )

    # Pass 3: even slabs across the extent for anyone still missing.
    n = max(len(order), 1)
    for idx, rid in enumerate(order):
        if rid in tops_by_rock:
            continue
        # Younger formations sit higher → lower idx is older → deeper top.
        # ``order`` here is oldest→youngest, so idx=0 is deepest.
        # Distribute tops between z_min (oldest top sits on basement) and
        # z_max (youngest top is the ground surface).
        slab_top = extent.z_min + (extent.depth * (idx + 1) / n)
        tops_by_rock[rid] = (slab_top, "inferred", None)

    bottoms_by_rock: dict[str, float] = {}
    for idx, rid in enumerate(order):
        if idx == 0:
            bottoms_by_rock[rid] = extent.z_min
        else:
            below_rid = order[idx - 1]
            bottoms_by_rock[rid] = tops_by_rock[below_rid][0]

    return _DepthResolution(
        tops_by_rock=tops_by_rock,
        bottoms_by_rock=bottoms_by_rock,
    )


def _top_horizon_for(
    rock_id: str,
    canonical: str,
    order: list[str],
    canonical_by_id: dict[str, str],
    horizons: dict[str, Any],
) -> float | None:
    """Return the *top* of ``rock_id`` using the fixture horizon dict.

    Horizon names in the fixture (``fill_base``, ``outwash_base`` …) are
    interface depths — i.e. the *base* of the formation they're named
    after. So the **top** of formation ``X`` equals the **base** of the
    formation immediately above ``X`` in the stratigraphic order.
    """

    # Special-case: the bedrock formation has its own "top" entry.
    horizon_key = _FIXTURE_HORIZON_KEYS_BY_NAME.get(canonical.lower())
    if horizon_key == "bedrock_top" and horizon_key in horizons:
        return float(horizons[horizon_key])

    try:
        idx = order.index(rock_id)
    except ValueError:
        return None

    # Topmost formation: anchor at the ground surface.
    if idx == len(order) - 1:
        if "ground_surface" in horizons:
            return float(horizons["ground_surface"])
        return 0.0

    # All other formations: top-of-X = base-of-formation-above-X.
    above_canonical = canonical_by_id[order[idx + 1]]
    above_key = _FIXTURE_HORIZON_KEYS_BY_NAME.get(above_canonical.lower())
    if above_key and above_key in horizons:
        return float(horizons[above_key])
    return None


def _contact_top_z(contact: Contact) -> float | None:
    """Convert a :class:`Contact` depth to an absolute z (negative below ground)."""

    if contact.depth_value is None:
        return None
    value = float(contact.depth_value)
    if contact.depth_unit == "ft":
        value *= _FT_TO_M
    if not math.isfinite(value):
        return None
    # Depth values quoted as positive numbers in PDFs almost always
    # mean "metres below ground". Treat them as such.
    return -abs(value)


def _aoi_positions(extent: ModelExtent) -> list[tuple[str, tuple[float, float]]]:
    """Five evenly-spread (x, y) positions inside ``extent``."""

    inset = 0.25
    cx, cy = extent.center_xy
    x_lo = extent.x_min + extent.width * inset
    x_hi = extent.x_max - extent.width * inset
    y_lo = extent.y_min + extent.height * inset
    y_hi = extent.y_max - extent.height * inset
    return [
        ("center", (cx, cy)),
        ("sw", (x_lo, y_lo)),
        ("nw", (x_lo, y_hi)),
        ("se", (x_hi, y_lo)),
        ("ne", (x_hi, y_hi)),
    ]


def _contact_evidence_by_top(
    llm_extraction: LLMExtraction | None,
    glossary: GeologyGlossary,
) -> dict[str, str]:
    """Map canonical top-formation name → first evidence quote."""

    out: dict[str, str] = {}
    if llm_extraction is None:
        return out
    for contact in llm_extraction.contacts:
        canonical = glossary.canonical(contact.top_formation)
        if canonical not in out and contact.evidence:
            out[canonical] = contact.evidence[0].quote
    return out


def _explicit_dips_by_canonical(
    llm_extraction: LLMExtraction | None,
    glossary: GeologyGlossary,
) -> dict[str, tuple[float, float, str | None]]:
    """Map canonical formation name → (dip°, azimuth°, evidence)."""

    out: dict[str, tuple[float, float, str | None]] = {}
    if llm_extraction is None:
        return out
    for structure in llm_extraction.structures:
        if structure.type != "dip" or structure.formation is None:
            continue
        if structure.value_degrees is None:
            continue
        canonical = glossary.canonical(structure.formation)
        if canonical in out:
            continue
        azimuth = structure.azimuth_degrees
        if azimuth is None or not (0.0 <= azimuth < 360.0):
            azimuth = _DEFAULT_AZIMUTH_DEGREES
        evidence = structure.evidence[0].quote if structure.evidence else None
        out[canonical] = (float(structure.value_degrees), float(azimuth), evidence)
    return out


def _extracted_contact_points(
    *,
    llm_extraction: LLMExtraction,
    contexts: list[_FormationContext],
    extent: ModelExtent,
    glossary: GeologyGlossary,
) -> list[SurfacePoint]:
    """One additional `extracted` SurfacePoint per LLM contact, anchored at the AOI center."""

    out: list[SurfacePoint] = []
    canonical_to_ctx = {ctx.canonical: ctx for ctx in contexts}
    cx, cy = extent.center_xy
    for contact in llm_extraction.contacts:
        ctx = canonical_to_ctx.get(glossary.canonical(contact.top_formation))
        if ctx is None:
            continue
        z = _contact_top_z(contact)
        if z is None:
            continue
        out.append(
            SurfacePoint(
                formation_id=ctx.rock_id,
                x=cx,
                y=cy,
                z=max(extent.z_min, min(z, extent.z_max)),
                source="extracted",
                confidence=contact.confidence,
                evidence_quote=(
                    contact.evidence[0].quote if contact.evidence else None
                ),
                note=(
                    f"Anchor pinned to AOI centre using extracted depth "
                    f"({contact.depth_value} {contact.depth_unit})."
                ),
            )
        )
    return out


def _rock_age_ma(rock: Any) -> float | None:
    """Convert an AST ``RockDefinition.age`` to Ma if the value is absolute."""

    age = getattr(rock, "age", None)
    if age is None:
        return None
    to_ma = getattr(age, "to_ma", None)
    if callable(to_ma):
        try:
            return float(to_ma())
        except (TypeError, ValueError):
            return None
    return None


def _summarise(
    *,
    formations: Sequence[FormationConstraint],
    surface_points: Sequence[SurfacePoint],
    orientations: Sequence[Orientation],
) -> dict[str, Any]:
    def _by_source(items: Sequence[Any]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for item in items:
            counts[item.source] += 1
        return dict(counts)

    return {
        "formation_count": len(formations),
        "surface_point_count": len(surface_points),
        "orientation_count": len(orientations),
        "formations_by_source": _by_source(formations),
        "surface_points_by_source": _by_source(surface_points),
        "orientations_by_source": _by_source(orientations),
        "intrusive_count": sum(1 for f in formations if f.is_intrusive),
    }


__all__ = [
    "ConstraintBuilder",
]


# Avoid unused-import warnings from the AST surface set; they're kept
# for documentation of the runtime contract.
_ = (DepositionEvent, IntrusionEvent, Structure)
