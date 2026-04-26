"""Page-aware document chunker.

Strategy:

1. Walk the extraction page-by-page.
2. Skip pages that contain only whitespace (still recorded by the
   PDF extractor for completeness).
3. If a page's text fits inside the target window, emit it as one
   chunk.
4. Otherwise sub-split the page at sentence/paragraph boundaries with a
   small overlap so the LLM never sees a half-sentence in isolation.

Determinism: chunk ids follow ``{document_id}-p{page_start:04d}-c{seq:02d}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from geo_nyc.documents.schemas import ExtractedPage, ExtractionResult
from geo_nyc.extraction.schemas import Chunk

# Sentence-ish boundary in order of preference: paragraph break, sentence
# end, then any whitespace as a last resort.
_BOUNDARY_PATTERNS = [
    re.compile(r"\n\s*\n"),
    re.compile(r"(?<=[\.\?\!])\s+"),
    re.compile(r"\s+"),
]


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    """Tunables for the chunker."""

    target_chars: int = 1800
    """Soft upper bound on a single chunk in characters (~450 tokens)."""

    overlap_chars: int = 150
    """Trailing characters from the previous chunk that the next one
    repeats so the LLM never loses cross-boundary context."""

    min_chunk_chars: int = 80
    """Skip emitting tiny tail chunks; merge them into the previous one."""


class Chunker:
    """Splits an :class:`ExtractionResult` into stable :class:`Chunk` objects."""

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self._config = config or ChunkerConfig()

    def chunk(self, extraction: ExtractionResult) -> list[Chunk]:
        chunks: list[Chunk] = []
        sequence = 0
        for page in extraction.pages:
            page_chunks = list(self._chunk_page(extraction.document_id, page, sequence))
            chunks.extend(page_chunks)
            sequence += len(page_chunks)
        return chunks

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _chunk_page(
        self,
        document_id: str,
        page: ExtractedPage,
        start_sequence: int,
    ) -> list[Chunk]:
        text = page.text or ""
        if not text.strip():
            return []

        if len(text) <= self._config.target_chars:
            return [
                self._build_chunk(
                    document_id=document_id,
                    page_start=page.page,
                    page_end=page.page,
                    sequence=start_sequence,
                    text=text,
                )
            ]

        out: list[Chunk] = []
        sub_segments = self._split_text(text)
        for offset, segment in enumerate(sub_segments):
            out.append(
                self._build_chunk(
                    document_id=document_id,
                    page_start=page.page,
                    page_end=page.page,
                    sequence=start_sequence + offset,
                    text=segment,
                )
            )
        return out

    def _split_text(self, text: str) -> list[str]:
        """Greedy windower with overlap and boundary preference."""

        segments: list[str] = []
        pos = 0
        n = len(text)
        target = self._config.target_chars
        overlap = self._config.overlap_chars
        min_chars = self._config.min_chunk_chars

        while pos < n:
            end = min(pos + target, n)
            if end < n:
                cut = self._best_boundary(text, pos, end)
                if cut > pos + min_chars:
                    end = cut
            segment = text[pos:end].strip()
            if segment:
                segments.append(segment)
            if end >= n:
                break
            pos = max(end - overlap, pos + 1)

        # Merge any tiny tail into the previous segment.
        if len(segments) >= 2 and len(segments[-1]) < min_chars:
            tail = segments.pop()
            segments[-1] = (segments[-1] + " " + tail).strip()
        return segments

    def _best_boundary(self, text: str, start: int, end: int) -> int:
        window_start = max(start + 1, end - self._config.target_chars // 4)
        snippet = text[window_start:end]
        if not snippet:
            return end
        for pattern in _BOUNDARY_PATTERNS:
            matches = list(pattern.finditer(snippet))
            if matches:
                last = matches[-1]
                return window_start + last.end()
        return end

    def _build_chunk(
        self,
        *,
        document_id: str,
        page_start: int,
        page_end: int,
        sequence: int,
        text: str,
    ) -> Chunk:
        chunk_id = f"{document_id}-p{page_start:04d}-c{sequence:02d}"
        stripped = text.strip()
        return Chunk(
            chunk_id=chunk_id,
            document_id=document_id,
            page_start=page_start,
            page_end=page_end,
            sequence=sequence,
            text=stripped,
            char_count=len(stripped),
            is_empty=not bool(stripped),
        )


def chunk_extraction(
    extraction: ExtractionResult, config: ChunkerConfig | None = None
) -> list[Chunk]:
    """Functional convenience wrapper."""

    return Chunker(config=config).chunk(extraction)


__all__ = ["Chunker", "ChunkerConfig", "chunk_extraction"]
