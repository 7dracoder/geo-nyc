"""Schemas for chunked + scored document content.

Chunks carry enough provenance (``document_id``, ``page_start``,
``page_end``) for the LLM extractor to cite evidence back into the
source PDF without ever forgetting where a quote came from.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Chunk(BaseModel):
    """A bounded slice of document text plus light metadata."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(..., description="Stable id of the form '{document_id}-p{page_start:04d}-c{seq:02d}'.")
    document_id: str
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    sequence: int = Field(..., ge=0, description="Monotonic 0-based ordinal within the document.")
    text: str
    char_count: int = Field(..., ge=0)
    is_empty: bool = False


class RankedChunk(Chunk):
    """Chunk decorated with a relevance score and matched keywords."""

    score: float = Field(..., ge=0.0)
    raw_score: float = Field(..., ge=0.0)
    keywords: list[str] = Field(default_factory=list)
    matched_categories: dict[str, int] = Field(default_factory=dict)


class RankedChunks(BaseModel):
    """Whole-document chunk artifact, sorted by descending score."""

    document_id: str
    chunk_count: int = Field(..., ge=0)
    page_count: int = Field(..., ge=0)
    chunks: list[RankedChunk] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Chunk", "RankedChunk", "RankedChunks"]
