"""Unit tests for :mod:`geo_nyc.extraction.relevance`."""

from __future__ import annotations

from geo_nyc.extraction.chunker import Chunker
from geo_nyc.extraction.relevance import RelevanceScorer, score_chunks
from geo_nyc.extraction.schemas import Chunk
from tests.test_chunker import _fake_extraction


def _chunks_for(*page_texts: str) -> list[Chunk]:
    return Chunker().chunk(_fake_extraction(*page_texts))


def test_geology_dense_chunk_outscores_filler() -> None:
    chunks = _chunks_for(
        "Today is a fine day in the city. The sky is blue and birds sing.",
        (
            "The Manhattan Schist contact with Inwood Marble was logged at 42 m depth "
            "in a borehole near the Bronx. Dip 35° to the south."
        ),
        "Page about lunch and traffic. No depth values here.",
    )

    ranked = score_chunks("d_test", chunks)

    assert ranked.chunk_count == 3
    assert ranked.chunks[0].page_start == 2
    assert ranked.chunks[0].score == 1.0
    assert ranked.summary["max_score"] == 1.0
    assert ranked.summary["non_zero_chunks"] >= 1


def test_zero_signal_document_yields_zero_scores() -> None:
    chunks = _chunks_for("Plain prose with no signal.", "Another empty page.")

    ranked = score_chunks("d_test", chunks)

    assert ranked.chunk_count == 2
    assert all(c.score == 0.0 for c in ranked.chunks)
    assert ranked.summary["max_score"] == 0.0
    assert ranked.summary["non_zero_chunks"] == 0


def test_empty_input_returns_empty_ranking() -> None:
    ranked = score_chunks("d_test", [])

    assert ranked.chunk_count == 0
    assert ranked.chunks == []
    assert ranked.summary["non_zero_chunks"] == 0


def test_keywords_and_categories_are_recorded() -> None:
    chunks = _chunks_for(
        "Manhattan Schist outcrop with strike 145° and dip 35°. Boring B-101 hit bedrock at 12 m."
    )

    ranked = score_chunks("d_test", chunks)
    top = ranked.chunks[0]

    assert "manhattan schist" in top.keywords
    assert "geology_terms" in top.matched_categories
    assert "depth_units" in top.matched_categories
    assert "dip_strike" in top.matched_categories
    assert top.matched_categories["geology_terms"] >= 1


def test_long_low_density_chunk_does_not_beat_focused_chunk() -> None:
    focused = (
        "Manhattan Schist contact at 12 m depth. Dip 35° and strike 145°."
    )
    diluted = focused + " " + ("Lorem ipsum dolor sit amet. " * 200)
    chunks = _chunks_for(focused, diluted)

    ranked = RelevanceScorer().rank("d_test", chunks)

    focused_chunk = next(c for c in ranked.chunks if c.page_start == 1)
    diluted_chunk = next(c for c in ranked.chunks if c.page_start == 2)
    assert focused_chunk.score >= diluted_chunk.score
