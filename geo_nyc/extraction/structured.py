"""Pydantic schemas for the LLM-driven structured extraction.

The pipeline asks Ollama for one of these payloads, validates it, then
hands it to Phase 6 (DSL builder). Field names mirror the schema example
in ``planning/part-2-design.md`` so the prompt can quote it directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Allowed rock_type literals match the DSL grammar (intrusive/volcanic
# split for igneous so Phase 6 can build ROCK statements directly).
RockType = Literal["sedimentary", "volcanic", "intrusive", "metamorphic"]
DepthUnit = Literal["m", "ft"]
StructureType = Literal["dip", "strike", "fault", "fold"]


class EvidenceRef(BaseModel):
    """A single quote linking a fact back into the source document."""

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(..., min_length=1)
    page: int = Field(..., ge=1)
    quote: str = Field(..., min_length=1, max_length=600)
    chunk_id: str | None = None

    @field_validator("quote")
    @classmethod
    def _strip_quote(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("quote cannot be blank")
        return cleaned


class Formation(BaseModel):
    """A named geological formation."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    rock_type: RockType | None = None
    aliases: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("formation name cannot be blank")
        return cleaned


class Contact(BaseModel):
    """A stratigraphic contact (top formation overlies bottom formation)."""

    model_config = ConfigDict(frozen=True)

    top_formation: str = Field(..., min_length=1)
    bottom_formation: str = Field(..., min_length=1)
    depth_value: float | None = None
    depth_unit: DepthUnit | None = None
    location_text: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class Structure(BaseModel):
    """A measured structural feature (dip/strike/fault/fold)."""

    model_config = ConfigDict(frozen=True)

    type: StructureType
    value_degrees: float | None = None
    azimuth_degrees: float | None = None
    formation: str | None = None
    location_text: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class LLMExtraction(BaseModel):
    """Top-level extraction object returned by the LLM."""

    model_config = ConfigDict(frozen=True)

    formations: list[Formation] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    structures: list[Structure] = Field(default_factory=list)
    notes: str | None = None


class StructuredValidationReport(BaseModel):
    """Sanity-check verdict over an :class:`LLMExtraction`."""

    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    meets_demo_minimum: bool = False
    normalized: LLMExtraction | None = None


__all__ = [
    "Contact",
    "DepthUnit",
    "EvidenceRef",
    "Formation",
    "LLMExtraction",
    "RockType",
    "Structure",
    "StructureType",
    "StructuredValidationReport",
]
