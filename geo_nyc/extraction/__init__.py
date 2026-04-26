"""Document chunking + relevance ranking + structured LLM extraction."""

from geo_nyc.extraction.chunker import Chunker, chunk_extraction
from geo_nyc.extraction.llm_extractor import (
    ExtractionAttempt,
    ExtractionError,
    ExtractionParseError,
    ExtractionRunResult,
    ExtractionValidationError,
    LLMExtractor,
)
from geo_nyc.extraction.relevance import (
    RELEVANCE_CATEGORIES,
    RelevanceScorer,
    score_chunks,
)
from geo_nyc.extraction.schemas import Chunk, RankedChunk, RankedChunks
from geo_nyc.extraction.structured import (
    Contact,
    DepthUnit,
    EvidenceRef,
    Formation,
    LLMExtraction,
    RockType,
    Structure,
    StructuredValidationReport,
    StructureType,
)
from geo_nyc.extraction.validator import (
    StructuredExtractionValidator,
    validate_extraction,
)

__all__ = [
    "RELEVANCE_CATEGORIES",
    "Chunk",
    "Chunker",
    "Contact",
    "DepthUnit",
    "EvidenceRef",
    "ExtractionAttempt",
    "ExtractionError",
    "ExtractionParseError",
    "ExtractionRunResult",
    "ExtractionValidationError",
    "Formation",
    "LLMExtraction",
    "LLMExtractor",
    "RankedChunk",
    "RankedChunks",
    "RelevanceScorer",
    "RockType",
    "Structure",
    "StructureType",
    "StructuredExtractionValidator",
    "StructuredValidationReport",
    "chunk_extraction",
    "score_chunks",
    "validate_extraction",
]
