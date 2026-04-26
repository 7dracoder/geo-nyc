"""Heuristic relevance scorer for NYC geology chunks.

The scorer is intentionally cheap and explainable: it counts hits in
five categories (geology terms, formation names, locations, rock
types, and numeric/structural patterns), normalises by chunk length,
then linearly rescales scores into ``[0, 1]`` across the document.

This is the first-pass version called out in the design doc — Phase 5
can swap it for an embedding-based scorer once we have one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from geo_nyc.extraction.schemas import Chunk, RankedChunk, RankedChunks

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


_GEOLOGY_TERMS: tuple[str, ...] = (
    "depth",
    "bedrock",
    "formation",
    "contact",
    "outcrop",
    "exposure",
    "boring",
    "drill",
    "tunnel",
    "fault",
    "fold",
    "strike",
    "dip",
    "thrust",
    "unconformity",
    "intrusion",
    "stratigraphy",
    "horizon",
)

_NYC_FORMATIONS: tuple[str, ...] = (
    "Manhattan Schist",
    "Inwood Marble",
    "Fordham Gneiss",
    "Walloomsac",
    "Hartland",
    "Wissahickon",
    "Newark Basin",
    "Palisades",
    "Lloyd Aquifer",
    "Magothy",
    "Raritan",
    "Glacial Till",
    "Glacial Outwash",
    "Anthropogenic Fill",
)

_NYC_LOCATIONS: tuple[str, ...] = (
    "Manhattan",
    "Bronx",
    "Queens",
    "Brooklyn",
    "Staten Island",
    "Inwood",
    "Harlem",
    "Battery",
    "Hudson",
    "East River",
    "Central Park",
)

_ROCK_TYPES: tuple[str, ...] = (
    "schist",
    "marble",
    "gneiss",
    "granite",
    "basalt",
    "shale",
    "sandstone",
    "limestone",
    "dolomite",
    "quartzite",
    "slate",
    "till",
    "outwash",
    "sedimentary",
    "igneous",
    "metamorphic",
)


def _word_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """Build a single case-insensitive regex matching any of ``words`` with
    word boundaries on both sides."""

    escaped = sorted({re.escape(w) for w in words}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


_PATTERN_GEOLOGY = _word_pattern(_GEOLOGY_TERMS)
_PATTERN_FORMATIONS = _word_pattern(_NYC_FORMATIONS)
_PATTERN_LOCATIONS = _word_pattern(_NYC_LOCATIONS)
_PATTERN_ROCK_TYPES = _word_pattern(_ROCK_TYPES)

# Numeric/structural patterns. Tuned to be specific enough to avoid
# triggering on bare page numbers.
_PATTERN_DEPTH = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ft|feet|m\b|meter|meters|metre|metres|in|inches)\b",
    re.IGNORECASE,
)
_PATTERN_DIP_STRIKE = re.compile(
    r"\b(?:dip|strike)\s*(?:of|=|:|\-)?\s*\d+(?:\.\d+)?\s*°?",
    re.IGNORECASE,
)
_PATTERN_AGE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:Ma|Ga|ka|million\s+years|billion\s+years)\b",
    re.IGNORECASE,
)
_PATTERN_ANGLE = re.compile(r"\b\d+(?:\.\d+)?\s*°")


# ---------------------------------------------------------------------------
# Categories + weights
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Category:
    name: str
    weight: float
    pattern: re.Pattern[str]


RELEVANCE_CATEGORIES: tuple[_Category, ...] = (
    _Category("geology_terms", 1.0, _PATTERN_GEOLOGY),
    _Category("nyc_formations", 2.5, _PATTERN_FORMATIONS),
    _Category("nyc_locations", 0.6, _PATTERN_LOCATIONS),
    _Category("rock_types", 1.2, _PATTERN_ROCK_TYPES),
    _Category("depth_units", 1.5, _PATTERN_DEPTH),
    _Category("dip_strike", 2.0, _PATTERN_DIP_STRIKE),
    _Category("ages", 1.0, _PATTERN_AGE),
    _Category("angles", 0.4, _PATTERN_ANGLE),
)


# Highest-signal hits land in the user-facing keyword list.
_KEYWORD_CATEGORIES: frozenset[str] = frozenset(
    {"geology_terms", "nyc_formations", "rock_types", "dip_strike"}
)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class RelevanceScorer:
    """Score :class:`Chunk` objects against the NYC geology vocabulary."""

    def __init__(
        self,
        *,
        categories: tuple[_Category, ...] = RELEVANCE_CATEGORIES,
        max_keywords: int = 12,
    ) -> None:
        self._categories = categories
        self._max_keywords = max_keywords

    def rank(self, document_id: str, chunks: list[Chunk]) -> RankedChunks:
        if not chunks:
            return RankedChunks(
                document_id=document_id,
                chunk_count=0,
                page_count=0,
                chunks=[],
                summary={"max_score": 0.0, "mean_score": 0.0, "non_zero_chunks": 0},
            )

        scored: list[RankedChunk] = []
        max_raw = 0.0
        for chunk in chunks:
            ranked = self._score_chunk(chunk)
            scored.append(ranked)
            if ranked.raw_score > max_raw:
                max_raw = ranked.raw_score

        # Linear rescale into [0, 1]. A document where nothing matches
        # collapses to all-zero scores, which is intentional — the
        # frontend can surface "low evidence" for those.
        normalised: list[RankedChunk] = []
        for ranked in scored:
            score = ranked.raw_score / max_raw if max_raw > 0 else 0.0
            normalised.append(ranked.model_copy(update={"score": round(score, 4)}))

        normalised.sort(key=lambda c: (-c.score, c.sequence))

        non_zero = sum(1 for c in normalised if c.score > 0)
        mean_score = sum(c.score for c in normalised) / len(normalised)
        page_count = max((c.page_end for c in normalised), default=0)
        summary = {
            "max_score": round(max(c.score for c in normalised), 4),
            "mean_score": round(mean_score, 4),
            "max_raw_score": round(max_raw, 4),
            "non_zero_chunks": non_zero,
        }
        return RankedChunks(
            document_id=document_id,
            chunk_count=len(normalised),
            page_count=page_count,
            chunks=normalised,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _score_chunk(self, chunk: Chunk) -> RankedChunk:
        text = chunk.text
        category_hits: dict[str, int] = {}
        keywords: dict[str, None] = {}
        weighted_total = 0.0

        for category in self._categories:
            matches = category.pattern.findall(text)
            count = len(matches)
            if count == 0:
                continue
            category_hits[category.name] = count
            weighted_total += category.weight * count
            if category.name in _KEYWORD_CATEGORIES:
                for token in matches:
                    cleaned = token.strip().lower()
                    if cleaned:
                        keywords[cleaned] = None
                        if len(keywords) >= self._max_keywords:
                            break

        # Normalise by length (per 1000 chars) so a giant low-density
        # chunk doesn't beat a focused dense one.
        length_factor = max(chunk.char_count / 1000.0, 1.0)
        raw_score = weighted_total / math.sqrt(length_factor)

        return RankedChunk(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            sequence=chunk.sequence,
            text=chunk.text,
            char_count=chunk.char_count,
            is_empty=chunk.is_empty,
            score=0.0,
            raw_score=round(raw_score, 4),
            keywords=list(keywords.keys()),
            matched_categories=category_hits,
        )


def score_chunks(document_id: str, chunks: list[Chunk]) -> RankedChunks:
    """Functional convenience wrapper."""

    return RelevanceScorer().rank(document_id, chunks)


__all__ = ["RELEVANCE_CATEGORIES", "RelevanceScorer", "score_chunks"]
