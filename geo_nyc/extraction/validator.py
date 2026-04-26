"""Sanity validator + unit normaliser for :class:`LLMExtraction`.

Layered checks (the order matters; later checks short-circuit on errors
collected earlier so the LLM gets the most useful feedback):

1. Structural — required field combinations (e.g. contacts must have
   distinct top/bottom formations).
2. Numeric — depth >= 0, dip in [0, 90], azimuth in [0, 360],
   confidence in [0, 1].
3. Reference — every referenced formation in contacts/structures must
   appear in ``formations``; every evidence quote must come from a
   chunk we actually showed the LLM.
4. Demo minimum — at least two formations, at least one contact or
   measured structure, at least one evidence quote.

The validator returns a :class:`StructuredValidationReport` plus a
*normalised* extraction (units converted to metres, formation names
trimmed). The original extraction is left untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from geo_nyc.extraction.schemas import RankedChunks
from geo_nyc.extraction.structured import (
    Contact,
    EvidenceRef,
    Formation,
    LLMExtraction,
    Structure,
    StructuredValidationReport,
)

_FT_TO_M = 0.3048


@dataclass(frozen=True, slots=True)
class _ChunkLookup:
    """Tiny helper for evidence checks."""

    by_id: dict[str, tuple[int, int]]  # chunk_id -> (page_start, page_end)
    pages_present: frozenset[int]

    @classmethod
    def from_ranked(cls, ranked: RankedChunks | None) -> _ChunkLookup:
        if ranked is None:
            return cls(by_id={}, pages_present=frozenset())
        by_id = {c.chunk_id: (c.page_start, c.page_end) for c in ranked.chunks}
        pages = {p for c in ranked.chunks for p in range(c.page_start, c.page_end + 1)}
        return cls(by_id=by_id, pages_present=frozenset(pages))


class StructuredExtractionValidator:
    """Runs the validation pipeline. Stateless; safe to share."""

    def validate(
        self,
        extraction: LLMExtraction,
        *,
        document_id: str | None = None,
        ranked_chunks: RankedChunks | None = None,
    ) -> StructuredValidationReport:
        errors: list[str] = []
        warnings: list[str] = []

        formation_names = {f.name.strip().lower() for f in extraction.formations}
        chunk_lookup = _ChunkLookup.from_ranked(ranked_chunks)

        normalised_formations = [
            self._validate_formation(f, errors, warnings, document_id, chunk_lookup, idx=i)
            for i, f in enumerate(extraction.formations)
        ]
        normalised_contacts = [
            self._validate_contact(
                c, errors, warnings, document_id, chunk_lookup, formation_names, idx=i
            )
            for i, c in enumerate(extraction.contacts)
        ]
        normalised_structures = [
            self._validate_structure(
                s, errors, warnings, document_id, chunk_lookup, formation_names, idx=i
            )
            for i, s in enumerate(extraction.structures)
        ]

        # Demo minimums -- emit warnings, not errors. The pipeline can
        # still succeed if the LLM only finds part of the answer.
        unique_formations = {f.name.strip().lower() for f in normalised_formations if f}
        evidence_total = (
            sum(len(f.evidence) for f in normalised_formations)
            + sum(len(c.evidence) for c in normalised_contacts)
            + sum(len(s.evidence) for s in normalised_structures)
        )
        meets_demo_minimum = (
            len(unique_formations) >= 2
            and (len(normalised_contacts) >= 1 or len(normalised_structures) >= 1)
            and evidence_total >= 1
        )
        if not meets_demo_minimum:
            warnings.append(
                "Demo minimum not met: need >=2 formations, >=1 contact or structure, "
                "and >=1 evidence quote."
            )

        is_valid = not errors
        normalised: LLMExtraction | None = None
        if is_valid:
            normalised = LLMExtraction(
                formations=normalised_formations,
                contacts=normalised_contacts,
                structures=normalised_structures,
                notes=extraction.notes,
            )

        return StructuredValidationReport(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            meets_demo_minimum=meets_demo_minimum,
            normalized=normalised,
        )

    # ------------------------------------------------------------------
    # Per-entity helpers
    # ------------------------------------------------------------------

    def _validate_formation(
        self,
        formation: Formation,
        errors: list[str],
        warnings: list[str],
        document_id: str | None,
        chunks: _ChunkLookup,
        *,
        idx: int,
    ) -> Formation:
        path = f"formations[{idx}] '{formation.name}'"
        if not formation.evidence:
            warnings.append(f"{path} has no evidence quote.")
        evidence = [
            self._validate_evidence(e, errors, warnings, document_id, chunks, where=path)
            for e in formation.evidence
        ]
        if formation.rock_type is None:
            warnings.append(f"{path} has unknown rock_type; downstream may skip it.")
        cleaned_aliases = sorted({a.strip() for a in formation.aliases if a.strip()})
        return Formation(
            name=formation.name.strip(),
            rock_type=formation.rock_type,
            aliases=cleaned_aliases,
            evidence=evidence,
        )

    def _validate_contact(
        self,
        contact: Contact,
        errors: list[str],
        warnings: list[str],
        document_id: str | None,
        chunks: _ChunkLookup,
        formation_names: set[str],
        *,
        idx: int,
    ) -> Contact:
        path = f"contacts[{idx}] '{contact.top_formation} / {contact.bottom_formation}'"
        if contact.top_formation.strip().lower() == contact.bottom_formation.strip().lower():
            errors.append(f"{path}: top_formation and bottom_formation must differ.")
        if contact.top_formation.strip().lower() not in formation_names:
            warnings.append(
                f"{path}: top_formation '{contact.top_formation}' not declared in formations."
            )
        if contact.bottom_formation.strip().lower() not in formation_names:
            warnings.append(
                f"{path}: bottom_formation '{contact.bottom_formation}' not declared in formations."
            )

        depth_value, depth_unit = self._normalise_depth(
            contact.depth_value, contact.depth_unit, errors, where=path
        )

        if not contact.evidence:
            warnings.append(f"{path} has no evidence quote.")
        evidence = [
            self._validate_evidence(e, errors, warnings, document_id, chunks, where=path)
            for e in contact.evidence
        ]
        return Contact(
            top_formation=contact.top_formation.strip(),
            bottom_formation=contact.bottom_formation.strip(),
            depth_value=depth_value,
            depth_unit=depth_unit,
            location_text=contact.location_text.strip() if contact.location_text else None,
            confidence=contact.confidence,
            evidence=evidence,
        )

    def _validate_structure(
        self,
        structure: Structure,
        errors: list[str],
        warnings: list[str],
        document_id: str | None,
        chunks: _ChunkLookup,
        formation_names: set[str],
        *,
        idx: int,
    ) -> Structure:
        path = f"structures[{idx}] '{structure.type}'"
        if structure.value_degrees is not None:
            v = structure.value_degrees
            if not math.isfinite(v):
                errors.append(f"{path}: value_degrees must be a finite number.")
            elif structure.type == "dip" and not (0.0 <= v <= 90.0):
                errors.append(f"{path}: dip must be in [0, 90]; got {v}.")
            elif structure.type == "strike" and not (0.0 <= v <= 360.0):
                errors.append(f"{path}: strike must be in [0, 360]; got {v}.")

        if structure.azimuth_degrees is not None:
            a = structure.azimuth_degrees
            if not math.isfinite(a) or not (0.0 <= a <= 360.0):
                errors.append(f"{path}: azimuth_degrees must be in [0, 360]; got {a}.")

        if (
            structure.formation
            and structure.formation.strip().lower() not in formation_names
        ):
            warnings.append(
                f"{path}: formation '{structure.formation}' not declared in formations."
            )

        evidence = [
            self._validate_evidence(e, errors, warnings, document_id, chunks, where=path)
            for e in structure.evidence
        ]
        if not evidence:
            warnings.append(f"{path} has no evidence quote.")
        return Structure(
            type=structure.type,
            value_degrees=structure.value_degrees,
            azimuth_degrees=structure.azimuth_degrees,
            formation=structure.formation.strip() if structure.formation else None,
            location_text=structure.location_text.strip() if structure.location_text else None,
            evidence=evidence,
        )

    def _validate_evidence(
        self,
        ev: EvidenceRef,
        errors: list[str],
        warnings: list[str],
        document_id: str | None,
        chunks: _ChunkLookup,
        *,
        where: str,
    ) -> EvidenceRef:
        if document_id and ev.document_id != document_id:
            errors.append(
                f"{where}: evidence.document_id={ev.document_id!r} does not match expected "
                f"{document_id!r}."
            )
        if chunks.by_id and ev.chunk_id is not None and ev.chunk_id not in chunks.by_id:
            errors.append(
                f"{where}: evidence.chunk_id={ev.chunk_id!r} is not present in the source chunks."
            )
        if chunks.pages_present and ev.page not in chunks.pages_present:
            warnings.append(
                f"{where}: evidence.page={ev.page} is outside the chunked page range."
            )
        return ev

    # ------------------------------------------------------------------
    # Numeric normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_depth(
        value: float | None,
        unit: str | None,
        errors: list[str],
        *,
        where: str,
    ) -> tuple[float | None, str | None]:
        if value is None:
            if unit is not None:
                # Unit alone is meaningless; downgrade to None silently.
                return None, None
            return None, None
        if not math.isfinite(value):
            errors.append(f"{where}: depth_value must be a finite number; got {value}.")
            return None, None
        if value < 0:
            errors.append(f"{where}: depth_value must be >= 0; got {value}.")
            return None, None
        if unit == "ft":
            return round(value * _FT_TO_M, 4), "m"
        if unit == "m" or unit is None:
            return value, "m"
        errors.append(f"{where}: depth_unit must be 'm' or 'ft'; got {unit!r}.")
        return None, None


def validate_extraction(
    extraction: LLMExtraction,
    *,
    document_id: str | None = None,
    ranked_chunks: RankedChunks | None = None,
) -> StructuredValidationReport:
    """Functional convenience wrapper."""

    return StructuredExtractionValidator().validate(
        extraction, document_id=document_id, ranked_chunks=ranked_chunks
    )


__all__ = ["StructuredExtractionValidator", "validate_extraction"]
