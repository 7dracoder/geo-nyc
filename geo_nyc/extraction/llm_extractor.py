"""LLM-driven structured extraction with a self-repair loop.

Flow:

1. Pick the top-K ranked chunks by score (or all chunks if scores are
   uniformly zero) and render the extraction prompt.
2. Ask the configured :class:`BaseLLMProvider` for JSON mode output.
3. Parse + Pydantic-validate the JSON.
4. Run :func:`validate_extraction` for structural and numeric sanity.
5. On failure, emit a repair prompt with the validation errors plus the
   prior raw output and try again, up to ``max_repair_attempts``.
6. Persist every attempt under ``run_dir/llm_attempts/`` so we always
   have an audit trail, even when extraction fails.

The whole orchestration is async-first so the FastAPI route can await
it without blocking; tests inject stub providers via constructor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from geo_nyc.ai.providers.base import BaseLLMProvider
from geo_nyc.config import Settings, get_settings
from geo_nyc.exceptions import GeoNYCError
from geo_nyc.extraction.schemas import RankedChunk, RankedChunks
from geo_nyc.extraction.structured import LLMExtraction, StructuredValidationReport
from geo_nyc.extraction.validator import (
    StructuredExtractionValidator,
    validate_extraction,
)
from geo_nyc.logging import get_logger
from geo_nyc.prompts import PromptTemplate, load_prompt

_LOG = get_logger(__name__)
_MAX_CHUNK_CHARS = 1800
_DEFAULT_TOP_K = 6
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


# ---------------------------------------------------------------------------
# Public errors
# ---------------------------------------------------------------------------


class ExtractionError(GeoNYCError):
    """Base for unrecoverable extractor failures."""


class ExtractionParseError(ExtractionError):
    """LLM output could not be parsed as JSON."""


class ExtractionValidationError(ExtractionError):
    """Final attempt failed schema/sanity validation."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExtractionAttempt:
    """A single LLM round-trip with full provenance."""

    attempt: int
    role: str  # "initial" | "repair"
    system_prompt: str
    user_prompt: str
    raw_output: str
    parsed: LLMExtraction | None
    parse_error: str | None
    validation: StructuredValidationReport | None
    duration_ms: int
    metadata: dict[str, object]


@dataclass(slots=True)
class ExtractionRunResult:
    """Outcome of the full extractor pipeline."""

    document_id: str
    succeeded: bool
    extraction: LLMExtraction | None
    validation: StructuredValidationReport | None
    attempts: list[ExtractionAttempt]
    selected_chunk_ids: list[str]


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class LLMExtractor:
    """Orchestrates the prompt → JSON → validate → repair pipeline."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        *,
        settings: Settings | None = None,
        validator: StructuredExtractionValidator | None = None,
        extraction_prompt: PromptTemplate | None = None,
        repair_prompt: PromptTemplate | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings or get_settings()
        self._validator = validator or StructuredExtractionValidator()
        self._extraction_prompt = extraction_prompt or load_prompt("nyc_geology_extraction")
        self._repair_prompt = repair_prompt or load_prompt("repair_extraction")

    async def extract(
        self,
        ranked: RankedChunks,
        *,
        document_id: str | None = None,
        run_dir: Path | None = None,
        top_k: int | None = None,
    ) -> ExtractionRunResult:
        document_id = document_id or ranked.document_id
        if ranked.chunk_count == 0:
            raise ExtractionError(
                "No ranked chunks were provided; nothing to send to the LLM."
            )

        attempts_dir = run_dir / "llm_attempts" if run_dir else None
        if attempts_dir is not None:
            attempts_dir.mkdir(parents=True, exist_ok=True)

        selected = self._select_chunks(ranked, top_k=top_k or _DEFAULT_TOP_K)
        chunks_block = self._render_chunks_block(selected)
        attempts: list[ExtractionAttempt] = []

        max_attempts = 1 + max(0, self._settings.llm_max_repair_attempts)
        for attempt_idx in range(1, max_attempts + 1):
            role = "initial" if attempt_idx == 1 else "repair"
            if role == "initial":
                system, user = self._extraction_prompt.render(
                    document_id=document_id, chunks_block=chunks_block
                )
            else:
                previous = attempts[-1]
                system, user = self._repair_prompt.render(
                    document_id=document_id,
                    chunks_block=chunks_block,
                    previous_json=previous.raw_output,
                    errors_block=self._format_errors(previous.validation),
                )

            attempt = await self._run_attempt(
                attempt_idx=attempt_idx,
                role=role,
                system_prompt=system,
                user_prompt=user,
                document_id=document_id,
                ranked=ranked,
            )
            attempts.append(attempt)
            self._persist_attempt(attempts_dir, attempt)

            if attempt.parsed is not None and attempt.validation is not None and attempt.validation.is_valid:
                _LOG.info(
                    "extraction_succeeded",
                    extra={
                        "document_id": document_id,
                        "attempt": attempt_idx,
                        "role": role,
                    },
                )
                return ExtractionRunResult(
                    document_id=document_id,
                    succeeded=True,
                    extraction=attempt.validation.normalized or attempt.parsed,
                    validation=attempt.validation,
                    attempts=attempts,
                    selected_chunk_ids=[c.chunk_id for c in selected],
                )

        last = attempts[-1]
        _LOG.warning(
            "extraction_failed",
            extra={
                "document_id": document_id,
                "attempts": len(attempts),
                "last_parse_error": last.parse_error,
                "last_errors": (last.validation.errors if last.validation else None),
            },
        )
        return ExtractionRunResult(
            document_id=document_id,
            succeeded=False,
            extraction=last.parsed if last.validation else None,
            validation=last.validation,
            attempts=attempts,
            selected_chunk_ids=[c.chunk_id for c in selected],
        )

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _select_chunks(ranked: RankedChunks, *, top_k: int) -> list[RankedChunk]:
        if not ranked.chunks:
            return []
        non_zero = [c for c in ranked.chunks if c.score > 0]
        pool = non_zero if non_zero else list(ranked.chunks)
        return pool[:top_k]

    @staticmethod
    def _render_chunks_block(chunks: list[RankedChunk]) -> str:
        rendered: list[str] = []
        for c in chunks:
            text = c.text
            if len(text) > _MAX_CHUNK_CHARS:
                text = text[:_MAX_CHUNK_CHARS].rstrip() + " …"
            header = (
                f"[chunk_id={c.chunk_id} | document_id={c.document_id} | "
                f"pages={c.page_start}-{c.page_end} | score={c.score:.2f}]"
            )
            rendered.append(f"{header}\n{text}")
        return "\n\n---\n\n".join(rendered)

    @staticmethod
    def _format_errors(validation: StructuredValidationReport | None) -> str:
        if validation is None:
            return "- The previous output could not be parsed as JSON. Return one valid JSON object."
        bullets = [f"- {err}" for err in validation.errors]
        if not bullets:
            bullets.append("- (no structured errors recorded)")
        return "\n".join(bullets)

    # ------------------------------------------------------------------
    # Single attempt
    # ------------------------------------------------------------------

    async def _run_attempt(
        self,
        *,
        attempt_idx: int,
        role: str,
        system_prompt: str,
        user_prompt: str,
        document_id: str,
        ranked: RankedChunks,
    ) -> ExtractionAttempt:
        started = datetime.now(UTC)
        response = await self._provider.generate_json(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=self._settings.llm_max_tokens,
        )
        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        raw_output = response.text

        parsed: LLMExtraction | None = None
        parse_error: str | None = None
        validation: StructuredValidationReport | None = None
        try:
            payload = self._parse_json(raw_output)
            parsed = LLMExtraction.model_validate(payload)
        except ExtractionParseError as exc:
            parse_error = str(exc)
        except ValidationError as exc:
            parse_error = f"Schema validation failed: {exc.error_count()} error(s)"
            validation = self._validation_from_pydantic(exc)
        else:
            validation = validate_extraction(
                parsed, document_id=document_id, ranked_chunks=ranked
            )

        return ExtractionAttempt(
            attempt=attempt_idx,
            role=role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_output=raw_output,
            parsed=parsed,
            parse_error=parse_error,
            validation=validation,
            duration_ms=elapsed_ms,
            metadata=dict(response.metadata),
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, object]:
        cleaned = raw.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        match = _JSON_OBJECT_RE.search(cleaned)
        if not match:
            raise ExtractionParseError(
                "LLM output did not contain a JSON object. First 200 chars: "
                + cleaned[:200]
            )
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ExtractionParseError(f"JSON decode error: {exc.msg} at char {exc.pos}") from exc

    @staticmethod
    def _validation_from_pydantic(exc: ValidationError) -> StructuredValidationReport:
        errors = [
            "{loc}: {msg}".format(
                loc=".".join(str(p) for p in err["loc"]),
                msg=err["msg"],
            )
            for err in exc.errors()
        ]
        return StructuredValidationReport(
            is_valid=False,
            errors=errors,
            warnings=[],
            meets_demo_minimum=False,
            normalized=None,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _persist_attempt(attempts_dir: Path | None, attempt: ExtractionAttempt) -> None:
        if attempts_dir is None:
            return
        record = {
            "attempt": attempt.attempt,
            "role": attempt.role,
            "duration_ms": attempt.duration_ms,
            "parse_error": attempt.parse_error,
            "metadata": attempt.metadata,
            "validation": (
                attempt.validation.model_dump() if attempt.validation else None
            ),
            "system_prompt": attempt.system_prompt,
            "user_prompt": attempt.user_prompt,
            "raw_output": attempt.raw_output,
            "parsed": attempt.parsed.model_dump() if attempt.parsed else None,
        }
        path = attempts_dir / f"attempt_{attempt.attempt:03d}_{attempt.role}.json"
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")


__all__ = [
    "ExtractionAttempt",
    "ExtractionError",
    "ExtractionParseError",
    "ExtractionRunResult",
    "ExtractionValidationError",
    "LLMExtractor",
]
