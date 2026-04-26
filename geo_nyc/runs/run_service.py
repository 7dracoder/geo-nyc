"""Run orchestrator: turns a request into on-disk artifacts.

The synchronous flow (fixture mode):

1. Allocate a run id and per-run directories under ``runs_dir``,
   ``exports_dir``, ``fields_dir``.
2. Resolve the fixture bundle (extraction JSON + DSL + extent).
3. Parse + validate the DSL via :mod:`geo_nyc.parsers.dsl`.
4. Build synthetic layer meshes and export them to ``model.glb``.
5. Derive the depth-to-bedrock scalar field and export to ``.npz``.
6. Write ``manifest.json`` + ``validation_report.json``.
7. Cache the manifest in-memory and return it.

The service is intentionally stateless across processes (the on-disk
manifest is the source of truth); the in-memory cache is only there to
avoid rereading JSON on hot paths within a single process.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from geo_nyc.ai import get_default_provider
from geo_nyc.config import Settings, get_settings
from geo_nyc.documents import DocumentService, get_document_service
from geo_nyc.exceptions import (
    DocumentNotFoundError,
    DSLValidationError,
    GeoNYCError,
    NotFoundError,
    RunError,
    RunNotFoundError,
)
from geo_nyc.extraction import Chunker, LLMExtractor, RelevanceScorer
from geo_nyc.extraction.llm_extractor import ExtractionRunResult
from geo_nyc.extraction.schemas import RankedChunks
from geo_nyc.extraction.structured import LLMExtraction
from geo_nyc.logging import get_logger
from geo_nyc.modeling.constraint_builder import ConstraintBuilder
from geo_nyc.modeling.constraints import GemPyInputs, GridResolution3D
from geo_nyc.modeling.extent import GridResolution
from geo_nyc.modeling.field_builder import (
    build_depth_to_bedrock_field_from_inputs,
    build_stub_depth_field,
)
from geo_nyc.modeling.field_export import export_field_to_npz
from geo_nyc.modeling.gempy_runner import GemPyRunner
from geo_nyc.modeling.mesh_export import export_layers_to_gltf
from geo_nyc.modeling.rbf_runner import RBFRunner
from geo_nyc.modeling.runner import EngineName, MeshRunner, MeshRunResult
from geo_nyc.modeling.synthetic_field import (
    FieldSource,
    ScalarField,
    build_depth_to_bedrock_field,
)
from geo_nyc.modeling.synthetic_mesh import LayerMesh, build_synthetic_layers
from geo_nyc.parsers.dsl import parse_and_validate
from geo_nyc.parsers.dsl.ast import Program
from geo_nyc.parsers.dsl.builder import build_dsl_from_extraction
from geo_nyc.parsers.dsl.validator import ValidationReport
from geo_nyc.runs.fixtures import load_fixture_bundle
from geo_nyc.runs.manifest import (
    Artifact,
    RunManifest,
    RunState,
    RunStatus,
    ValidationIssue,
    ValidationReportPayload,
)

_LOG = get_logger(__name__)
_DEFAULT_FIXTURE = "nyc_demo"


@dataclass(frozen=True, slots=True)
class _MeshOutcome:
    """Bookkeeping returned by :meth:`RunService._compute_mesh`."""

    result: MeshRunResult
    attempts: list[dict[str, Any]]

    @property
    def layers(self) -> list[LayerMesh]:
        return self.result.layers

    def summary(self) -> dict[str, Any]:
        return {
            "engine": self.result.engine,
            "layer_count": len(self.result.layers),
            "duration_ms": self.result.duration_ms,
            "fallback_from": list(self.result.fallback_from),
            "vertex_count": int(sum(layer.vertex_count() for layer in self.result.layers)),
            "face_count": int(sum(layer.face_count() for layer in self.result.layers)),
            "attempts": self.attempts,
        }


@dataclass(frozen=True, slots=True)
class _FieldOutcome:
    """Bookkeeping returned by :meth:`RunService._compute_field`.

    ``engine`` is the field's logical source (``rbf`` / ``synthetic`` /
    ``stub``) — distinct from the *mesh* engine because the field can
    fall back independently if the GemPy / RBF surface points are
    unusable for bedrock.
    """

    field: ScalarField
    engine: FieldSource
    fallback_from: tuple[FieldSource, ...]
    attempts: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "fallback_from": list(self.fallback_from),
            "shape": list(self.field.values.shape),
            "resolution_m": self.field.resolution_m,
            "units": self.field.units,
            "stats": self.field.stats(),
            "has_mask": self.field.has_mask,
            "attempts": self.attempts,
        }


class RunService:
    """Owns the on-disk and in-memory state for ``/api/run`` requests."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        document_service: DocumentService | None = None,
        chunker: Chunker | None = None,
        scorer: RelevanceScorer | None = None,
        llm_extractor: LLMExtractor | None = None,
        constraint_builder: ConstraintBuilder | None = None,
        mesh_runners: list[MeshRunner] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._document_service = document_service
        self._chunker = chunker or Chunker()
        self._scorer = scorer or RelevanceScorer()
        self._llm_extractor = llm_extractor
        self._constraint_builder = constraint_builder or ConstraintBuilder(
            grid_resolution=GridResolution3D(),
        )
        # Default precedence: real GemPy → scipy RBF → synthetic stub.
        # The synthetic runner is *always* there as a last-resort fallback
        # so the demo never breaks even if scipy/GemPy explode at runtime.
        self._mesh_runners: list[MeshRunner] = (
            mesh_runners
            if mesh_runners is not None
            else [GemPyRunner(), RBFRunner()]
        )
        self._cache: dict[str, RunState] = {}
        self._lock = Lock()

    def _documents(self) -> DocumentService:
        # Lazy-resolve the singleton so test fixtures that build the
        # service inline can still rely on the default.
        if self._document_service is None:
            self._document_service = get_document_service()
        return self._document_service

    def _extractor(self) -> LLMExtractor:
        if self._llm_extractor is None:
            self._llm_extractor = LLMExtractor(
                get_default_provider(), settings=self._settings
            )
        return self._llm_extractor

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        request_payload: dict[str, Any] | None = None,
        fixture_name: str = _DEFAULT_FIXTURE,
    ) -> RunManifest:
        """Synchronous wrapper around :meth:`acreate_run`.

        Tests and CLI tools can call this directly; the FastAPI route uses
        the native ``async`` entry point to avoid creating a nested event
        loop. The wrapper raises if it is invoked from inside a running
        event loop, since ``asyncio.run`` cannot recurse there.
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.acreate_run(
                    request_payload=request_payload, fixture_name=fixture_name
                )
            )
        raise RunError(
            "RunService.create_run() called from within an event loop. "
            "Use 'await service.acreate_run(...)' instead."
        )

    async def acreate_run(
        self,
        *,
        request_payload: dict[str, Any] | None = None,
        fixture_name: str = _DEFAULT_FIXTURE,
    ) -> RunManifest:
        """Async pipeline: chunks → optional LLM extraction → fixture model."""

        if not self._settings.use_fixtures:
            # Phase 11 only ships fixture mode; the LLM/GemPy paths are
            # implemented in later phases.
            raise RunError(
                "GEO_NYC_USE_FIXTURES is false but only fixture mode is wired up "
                "today. Set GEO_NYC_USE_FIXTURES=true or wait for Phase 5/8."
            )

        document_id = (request_payload or {}).get("document_id")
        # Validate inputs that should 404 (not "run failed") *before*
        # we allocate run dirs or persist any state.
        ranked_chunks: RankedChunks | None = None
        if document_id:
            try:
                extraction = self._documents().get_extraction(document_id)
            except DocumentNotFoundError as exc:
                raise DocumentNotFoundError(
                    f"Document '{document_id}' has no stored extraction yet. "
                    f"Call POST /api/documents/{{id}}/extract first. ({exc})"
                ) from exc
            chunks = self._chunker.chunk(extraction)
            ranked_chunks = self._scorer.rank(document_id, chunks)

        run_id = self._allocate_run_id()
        started = time.monotonic()
        created_at = datetime.now(UTC)

        run_dir = self._settings.runs_dir / run_id
        export_dir = self._settings.exports_dir / run_id
        field_dir = self._settings.fields_dir / run_id
        for d in (run_dir, export_dir, field_dir):
            d.mkdir(parents=True, exist_ok=True)

        mode = "document_chunks+fixture" if document_id else "fixture"

        manifest = RunManifest(
            run_id=run_id,
            status=RunStatus.RUNNING,
            created_at=created_at,
            updated_at=created_at,
            mode=mode,
            request=dict(request_payload or {}),
        )
        self._remember(RunState(run_id=run_id, status=RunStatus.RUNNING, manifest=manifest))

        try:
            artifacts: list[Artifact] = []
            chunk_summary: dict[str, Any] | None = None
            llm_summary: dict[str, Any] | None = None
            dsl_summary: dict[str, Any] | None = None
            llm_extraction: LLMExtraction | None = None
            if ranked_chunks is not None and document_id:
                chunks_artifact, chunk_summary = self._persist_ranked_chunks(
                    document_id=document_id,
                    ranked=ranked_chunks,
                    run_dir=run_dir,
                )
                artifacts.append(chunks_artifact)

                if (request_payload or {}).get("use_llm"):
                    (
                        llm_artifacts,
                        llm_summary,
                        llm_extraction,
                    ) = await self._run_llm_extraction(
                        document_id=document_id,
                        ranked=ranked_chunks,
                        run_dir=run_dir,
                        top_k=(request_payload or {}).get("top_k_chunks"),
                    )
                    artifacts.extend(llm_artifacts)
                    mode = "document_llm+fixture"

                    if llm_extraction is not None:
                        dsl_artifacts, dsl_summary = self._run_dsl_build(
                            extraction=llm_extraction,
                            run_dir=run_dir,
                        )
                        artifacts.extend(dsl_artifacts)
                        if dsl_summary.get("succeeded"):
                            mode = "document_llm_dsl+fixture"

            bundle = load_fixture_bundle(self._settings.fixtures_dir, fixture_name)
            program, report = parse_and_validate(bundle.dsl_text)
            if not report.is_valid:
                raise DSLValidationError(_format_report(report))

            gempy_artifact, gempy_summary = self._build_gempy_inputs(
                program=program,
                bundle_extraction=bundle.extraction,
                bundle_extent=bundle.extent,
                bundle_crs=bundle.crs,
                llm_extraction=llm_extraction,
                run_dir=run_dir,
                run_id=run_id,
                document_id=document_id,
                mode_label=mode,
            )
            if gempy_artifact is not None:
                artifacts.append(gempy_artifact)

            mesh_outcome = self._compute_mesh(
                program=program,
                bundle=bundle,
                run_id=run_id,
                gempy_inputs=self._reload_gempy_inputs(run_dir),
            )
            layers = mesh_outcome.layers
            if not layers:
                raise RunError("Fixture program produced zero layers; nothing to render.")

            artifacts.append(
                self._copy_text(
                    bundle.dsl_text,
                    run_dir / "dsl.txt",
                    kind="dsl",
                    media_type="text/plain",
                    description="Canonical DSL emitted from the extraction.",
                    relative_root=run_dir,
                    url=None,
                )
            )
            artifacts.append(
                self._copy_json(
                    bundle.extraction,
                    run_dir / "extraction.json",
                    kind="extraction",
                    media_type="application/json",
                    description="Source extraction payload (fixture).",
                    relative_root=run_dir,
                    url=None,
                )
            )

            mesh_path = export_dir / "model.glb"
            export_layers_to_gltf(layers, mesh_path, extent=bundle.extent)
            artifacts.append(
                Artifact(
                    kind="mesh",
                    filename=mesh_path.name,
                    relative_path=str(mesh_path.relative_to(self._settings.exports_dir)),
                    url=self._public_url("exports", run_id, mesh_path.name),
                    bytes=mesh_path.stat().st_size,
                    media_type="model/gltf-binary",
                    description=(
                        f"Layered subsurface mesh produced by the "
                        f"{mesh_outcome.result.engine!r} runner."
                    ),
                    metadata={
                        "engine": mesh_outcome.result.engine,
                        "fallback_from": list(mesh_outcome.result.fallback_from),
                        "duration_ms": mesh_outcome.result.duration_ms,
                        "surface_ids": [layer.surface_id for layer in layers],
                        "vertex_count": sum(layer.vertex_count() for layer in layers),
                        "face_count": sum(layer.face_count() for layer in layers),
                    },
                )
            )

            field_outcome = self._compute_field(
                run_id=run_id,
                gempy_inputs=self._reload_gempy_inputs(run_dir),
                mesh_outcome=mesh_outcome,
                bundle_extent=bundle.extent,
                bundle_crs=bundle.crs,
            )
            field_path = field_dir / "depth_to_bedrock.npz"
            npz_path, meta_path = export_field_to_npz(
                field_outcome.field,
                field_path,
                run_id=run_id,
                extra_metadata={
                    "engine": field_outcome.engine,
                    "fallback_from": list(field_outcome.fallback_from),
                    "mesh_engine": mesh_outcome.result.engine,
                },
            )
            artifacts.append(
                Artifact(
                    kind="field",
                    filename=npz_path.name,
                    relative_path=str(npz_path.relative_to(self._settings.fields_dir)),
                    url=self._public_url("fields", run_id, npz_path.name),
                    bytes=npz_path.stat().st_size,
                    media_type="application/octet-stream",
                    description="Depth-to-bedrock scalar field on a regular grid.",
                    metadata={
                        "name": field_outcome.field.name,
                        "units": field_outcome.field.units,
                        "crs": field_outcome.field.crs,
                        "source": field_outcome.field.source,
                        "resolution_m": field_outcome.field.resolution_m,
                        "shape": list(field_outcome.field.values.shape),
                    },
                )
            )
            artifacts.append(
                Artifact(
                    kind="field_meta",
                    filename=meta_path.name,
                    relative_path=str(meta_path.relative_to(self._settings.fields_dir)),
                    url=self._public_url("fields", run_id, meta_path.name),
                    bytes=meta_path.stat().st_size,
                    media_type="application/json",
                    description="Sidecar metadata for the depth field.",
                )
            )

            payload = _report_to_payload(report)
            (run_dir / "validation_report.json").write_text(
                payload.model_dump_json(indent=2), encoding="utf-8"
            )
            artifacts.append(
                Artifact(
                    kind="validation_report",
                    filename="validation_report.json",
                    relative_path="validation_report.json",
                    url="",  # not statically served; surfaced via /api/run
                    bytes=(run_dir / "validation_report.json").stat().st_size,
                    media_type="application/json",
                    description="Structural validation of the DSL Program.",
                )
            )

            duration_ms = int((time.monotonic() - started) * 1000)
            manifest = manifest.model_copy(
                update={
                    "status": RunStatus.SUCCEEDED,
                    "updated_at": datetime.now(UTC),
                    "mode": mode,
                    "artifacts": artifacts,
                    "validation": payload,
                    "layer_summary": _layer_summary(layers, program),
                    "extent": asdict(bundle.extent),
                    "chunk_summary": chunk_summary,
                    "llm_summary": llm_summary,
                    "dsl_summary": dsl_summary,
                    "gempy_inputs_summary": gempy_summary,
                    "mesh_summary": mesh_outcome.summary(),
                    "field_summary": field_outcome.summary(),
                    "duration_ms": duration_ms,
                }
            )
            (run_dir / "manifest.json").write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            self._remember(
                RunState(run_id=run_id, status=RunStatus.SUCCEEDED, manifest=manifest)
            )
            _LOG.info("run_succeeded", extra={"run_id": run_id, "duration_ms": duration_ms})
            return manifest

        except GeoNYCError as exc:
            return self._fail_run(manifest, run_dir, str(exc))
        except Exception as exc:
            _LOG.exception("run_unexpected_error", extra={"run_id": run_id})
            return self._fail_run(manifest, run_dir, f"Unexpected error: {exc}")

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> RunManifest:
        with self._lock:
            cached = self._cache.get(run_id)
        if cached is not None:
            return cached.manifest

        manifest_path = self._settings.runs_dir / run_id / "manifest.json"
        if not manifest_path.is_file():
            raise RunNotFoundError(f"No run with id={run_id!r}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = RunManifest.model_validate(data)
        except Exception as exc:
            raise RunError(f"Manifest for run {run_id!r} is corrupt: {exc}") from exc

        with self._lock:
            self._cache[run_id] = RunState(
                run_id=run_id, status=manifest.status, manifest=manifest
            )
        return manifest

    def list_runs(self, limit: int = 50) -> list[RunManifest]:
        runs: list[RunManifest] = []
        if not self._settings.runs_dir.is_dir():
            return runs
        ids = sorted(
            (p.name for p in self._settings.runs_dir.iterdir() if p.is_dir()),
            reverse=True,
        )
        for run_id in ids[:limit]:
            try:
                runs.append(self.get_run(run_id))
            except (NotFoundError, RunError):
                continue
        return runs

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _allocate_run_id(self) -> str:
        # Sortable by creation time. Format: ``r_YYYYMMDDhhmmss_<8hex>``.
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"r_{ts}_{uuid.uuid4().hex[:8]}"

    def _public_url(self, mount: str, run_id: str, filename: str) -> str:
        base = self._settings.public_base_url.rstrip("/")
        return f"{base}/static/{mount}/{run_id}/{filename}"

    def _copy_text(
        self,
        text: str,
        dest: Path,
        *,
        kind: str,
        media_type: str,
        description: str,
        relative_root: Path,
        url: str | None,
    ) -> Artifact:
        dest.write_text(text, encoding="utf-8")
        return Artifact(
            kind=kind,
            filename=dest.name,
            relative_path=str(dest.relative_to(relative_root.parent)),
            url=url or "",
            bytes=dest.stat().st_size,
            media_type=media_type,
            description=description,
        )

    def _copy_json(
        self,
        data: Any,
        dest: Path,
        *,
        kind: str,
        media_type: str,
        description: str,
        relative_root: Path,
        url: str | None,
    ) -> Artifact:
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return Artifact(
            kind=kind,
            filename=dest.name,
            relative_path=str(dest.relative_to(relative_root.parent)),
            url=url or "",
            bytes=dest.stat().st_size,
            media_type=media_type,
            description=description,
        )

    async def _run_llm_extraction(
        self,
        *,
        document_id: str,
        ranked: RankedChunks,
        run_dir: Path,
        top_k: int | None,
    ) -> tuple[list[Artifact], dict[str, Any], LLMExtraction | None]:
        """Run :class:`LLMExtractor`, persist its output, return artifacts + summary.

        Failures (parse, schema, validation) do *not* abort the run --
        we record an ``llm_extraction_failed`` artifact and let the
        fixture model still produce visible output for the demo.
        """

        result: ExtractionRunResult = await self._extractor().extract(
            ranked,
            document_id=document_id,
            run_dir=run_dir,
            top_k=top_k,
        )

        artifacts: list[Artifact] = []
        if result.succeeded and result.extraction is not None:
            dest = run_dir / "llm_extraction.json"
            dest.write_text(
                result.extraction.model_dump_json(indent=2), encoding="utf-8"
            )
            artifacts.append(
                Artifact(
                    kind="llm_extraction",
                    filename=dest.name,
                    relative_path=dest.name,
                    url="",
                    bytes=dest.stat().st_size,
                    media_type="application/json",
                    description="Structured geology extraction produced by Ollama.",
                    metadata={
                        "document_id": document_id,
                        "attempts": len(result.attempts),
                        "selected_chunk_ids": result.selected_chunk_ids,
                    },
                )
            )

        summary: dict[str, Any] = {
            "document_id": document_id,
            "succeeded": result.succeeded,
            "attempts": len(result.attempts),
            "selected_chunk_ids": result.selected_chunk_ids,
            "meets_demo_minimum": (
                bool(result.validation.meets_demo_minimum)
                if result.validation
                else False
            ),
        }
        if result.validation is not None:
            summary["error_count"] = len(result.validation.errors)
            summary["warning_count"] = len(result.validation.warnings)
            if not result.succeeded:
                summary["last_errors"] = result.validation.errors[:5]
        if result.extraction is not None:
            summary["formation_count"] = len(result.extraction.formations)
            summary["contact_count"] = len(result.extraction.contacts)
            summary["structure_count"] = len(result.extraction.structures)

        attempts_dir = run_dir / "llm_attempts"
        if attempts_dir.exists():
            artifacts.append(
                Artifact(
                    kind="llm_attempts",
                    filename=attempts_dir.name,
                    relative_path=attempts_dir.name,
                    url="",
                    bytes=sum(p.stat().st_size for p in attempts_dir.glob("*.json")),
                    media_type="application/json",
                    description="Per-attempt audit trail of the LLM extraction.",
                    metadata={"attempt_count": len(result.attempts)},
                )
            )

        _LOG.info(
            "llm_extraction_completed",
            extra={
                "run_id": run_dir.name,
                "document_id": document_id,
                "succeeded": result.succeeded,
                "attempts": len(result.attempts),
            },
        )
        extraction = result.extraction if result.succeeded else None
        return artifacts, summary, extraction

    def _compute_mesh(
        self,
        *,
        program: Program,
        bundle: Any,
        run_id: str,
        gempy_inputs: GemPyInputs | None,
    ) -> _MeshOutcome:
        """Run the configured runners in priority order, falling back on failure.

        ``gempy_inputs`` may be ``None`` if Phase 7 had no payload to
        produce — in that case we skip GemPy/RBF (both need them) and
        go straight to the synthetic stub.
        """

        attempts: list[dict[str, Any]] = []
        fallbacks: list[EngineName] = []
        result: MeshRunResult | None = None

        if gempy_inputs is not None:
            for runner in self._mesh_runners:
                if not runner.is_available():
                    attempts.append(
                        {
                            "engine": runner.name,
                            "skipped": True,
                            "reason": "not_available",
                        }
                    )
                    fallbacks.append(runner.name)
                    continue
                try:
                    candidate = runner.run(gempy_inputs)
                except Exception as exc:
                    _LOG.warning(
                        "mesh_runner_failed",
                        extra={
                            "run_id": run_id,
                            "engine": runner.name,
                            "error": str(exc),
                        },
                    )
                    attempts.append(
                        {
                            "engine": runner.name,
                            "skipped": False,
                            "succeeded": False,
                            "error": str(exc),
                        }
                    )
                    fallbacks.append(runner.name)
                    continue

                if candidate.is_empty:
                    attempts.append(
                        {
                            "engine": runner.name,
                            "skipped": False,
                            "succeeded": False,
                            "reason": "empty_output",
                        }
                    )
                    fallbacks.append(runner.name)
                    continue

                attempts.append(
                    {
                        "engine": runner.name,
                        "skipped": False,
                        "succeeded": True,
                        "duration_ms": candidate.duration_ms,
                        "layer_count": len(candidate.layers),
                    }
                )
                result = MeshRunResult(
                    engine=candidate.engine,
                    layers=candidate.layers,
                    duration_ms=candidate.duration_ms,
                    metadata=candidate.metadata,
                    fallback_from=tuple(fallbacks),
                )
                break

        if result is None:
            # Synthetic last-resort: never fails, never empty for a
            # validated program.
            timer_start = time.perf_counter_ns()
            synthetic_layers = build_synthetic_layers(
                program,
                bundle.extent,
                resolution=GridResolution(),
                color_overrides=_color_overrides_from_extraction(bundle.extraction),
            )
            duration_ms = int((time.perf_counter_ns() - timer_start) / 1_000_000)
            attempts.append(
                {
                    "engine": "synthetic",
                    "skipped": False,
                    "succeeded": True,
                    "duration_ms": duration_ms,
                    "layer_count": len(synthetic_layers),
                }
            )
            result = MeshRunResult(
                engine="synthetic",
                layers=synthetic_layers,
                duration_ms=duration_ms,
                metadata={"reason": "fallback_chain"},
                fallback_from=tuple(fallbacks),
            )

        _LOG.info(
            "mesh_runner_selected",
            extra={
                "run_id": run_id,
                "engine": result.engine,
                "fallback_from": list(result.fallback_from),
                "layer_count": len(result.layers),
            },
        )
        return _MeshOutcome(result=result, attempts=attempts)

    def _compute_field(
        self,
        *,
        run_id: str,
        gempy_inputs: GemPyInputs | None,
        mesh_outcome: _MeshOutcome,
        bundle_extent: Any,
        bundle_crs: str,
    ) -> _FieldOutcome:
        """Pick the highest-fidelity available depth-to-bedrock builder.

        Priority order:

        1. **From ``GemPyInputs``** when the constraint builder produced
           at least one bedrock surface point. This is the only path
           that's actually driven by the LLM-extracted geometry, so the
           resulting field's source matches the mesh engine
           (``rbf`` / ``gempy``).
        2. **From the mesh layers** when the mesh runner produced
           non-empty output. Useful when a synthetic-runner fallback
           kicked in but we still want a field that's visually
           congruent with the rendered mesh.
        3. **Stub** — last resort, deterministic, never fails.
        """

        attempts: list[dict[str, Any]] = []
        fallbacks: list[FieldSource] = []

        if gempy_inputs is not None and gempy_inputs.surface_points:
            try:
                field = build_depth_to_bedrock_field_from_inputs(
                    gempy_inputs,
                    extent=bundle_extent,
                    source=_field_source_for_engine(mesh_outcome.result.engine),
                )
                attempts.append(
                    {
                        "engine": field.source,
                        "skipped": False,
                        "succeeded": True,
                        "via": "gempy_inputs",
                    }
                )
                _LOG.info(
                    "field_built_from_inputs",
                    extra={
                        "run_id": run_id,
                        "engine": field.source,
                        "shape": list(field.values.shape),
                    },
                )
                return _FieldOutcome(
                    field=field,
                    engine=field.source,
                    fallback_from=tuple(fallbacks),
                    attempts=attempts,
                )
            except Exception as exc:
                _LOG.warning(
                    "field_from_inputs_failed",
                    extra={"run_id": run_id, "error": str(exc)},
                )
                attempts.append(
                    {
                        "engine": "rbf",
                        "skipped": False,
                        "succeeded": False,
                        "error": str(exc),
                        "via": "gempy_inputs",
                    }
                )
                fallbacks.append("rbf")

        if mesh_outcome.layers:
            try:
                source: FieldSource = (
                    "synthetic"
                    if mesh_outcome.result.engine == "synthetic"
                    else _field_source_for_engine(mesh_outcome.result.engine)
                )
                field = build_depth_to_bedrock_field(
                    mesh_outcome.layers,
                    bundle_extent,
                    crs=bundle_crs,
                    resolution=GridResolution(),
                    source=source,
                )
                attempts.append(
                    {
                        "engine": field.source,
                        "skipped": False,
                        "succeeded": True,
                        "via": "mesh_layers",
                    }
                )
                _LOG.info(
                    "field_built_from_layers",
                    extra={
                        "run_id": run_id,
                        "engine": field.source,
                        "shape": list(field.values.shape),
                    },
                )
                return _FieldOutcome(
                    field=field,
                    engine=field.source,
                    fallback_from=tuple(fallbacks),
                    attempts=attempts,
                )
            except Exception as exc:
                _LOG.warning(
                    "field_from_layers_failed",
                    extra={"run_id": run_id, "error": str(exc)},
                )
                attempts.append(
                    {
                        "engine": "synthetic",
                        "skipped": False,
                        "succeeded": False,
                        "error": str(exc),
                        "via": "mesh_layers",
                    }
                )
                fallbacks.append("synthetic")

        # Final fallback: deterministic stub. Never raises for a valid
        # extent. We keep this path live even when previous stages
        # succeeded so the demo can be salvaged from a corrupted field.
        stub = build_stub_depth_field(bundle_extent, crs=bundle_crs)
        attempts.append(
            {"engine": "stub", "skipped": False, "succeeded": True, "via": "stub"}
        )
        _LOG.info(
            "field_built_from_stub",
            extra={"run_id": run_id, "fallback_from": list(fallbacks)},
        )
        return _FieldOutcome(
            field=stub,
            engine="stub",
            fallback_from=tuple(fallbacks),
            attempts=attempts,
        )

    def _reload_gempy_inputs(self, run_dir: Path) -> GemPyInputs | None:
        """Re-read ``gempy_inputs.json`` so the mesh runner sees the canonical payload.

        We round-trip through disk (rather than holding the in-memory
        :class:`GemPyInputs`) so the runner only ever consumes data the
        manifest has already advertised — keeps the contract honest.
        """

        path = run_dir / "gempy_inputs.json"
        if not path.is_file():
            return None
        try:
            return GemPyInputs.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            _LOG.warning(
                "gempy_inputs_reload_failed",
                extra={"path": str(path), "error": str(exc)},
            )
            return None

    def _build_gempy_inputs(
        self,
        *,
        program: Program,
        bundle_extraction: dict[str, Any],
        bundle_extent: Any,
        bundle_crs: str,
        llm_extraction: LLMExtraction | None,
        run_dir: Path,
        run_id: str,
        document_id: str | None,
        mode_label: str,
    ) -> tuple[Artifact | None, dict[str, Any] | None]:
        """Phase 7: emit ``gempy_inputs.json`` from the validated Program.

        We always run this for fixture-mode and LLM-mode alike — the
        builder cleanly degrades to ``inferred`` / ``fixture`` provenance
        when there's no extracted depth data. Failures are non-fatal so
        the rest of the pipeline (mesh + field) keeps producing output.
        """

        try:
            inputs: GemPyInputs = self._constraint_builder.build(
                program=program,
                extent=bundle_extent,
                crs=bundle_crs,
                llm_extraction=llm_extraction,
                fixture_extraction=bundle_extraction,
                mode_label=mode_label,
                run_id=run_id,
                document_id=document_id,
            )
        except Exception as exc:
            _LOG.exception("gempy_inputs_build_failed", extra={"run_id": run_id})
            return None, {"succeeded": False, "error": str(exc)}

        dest = run_dir / "gempy_inputs.json"
        dest.write_text(inputs.model_dump_json(indent=2), encoding="utf-8")
        artifact = Artifact(
            kind="gempy_inputs",
            filename=dest.name,
            relative_path=dest.name,
            url="",
            bytes=dest.stat().st_size,
            media_type="application/json",
            description=(
                "GemPy-shaped inputs (formations, surface points, orientations) "
                "with per-constraint provenance."
            ),
            metadata={
                "formation_count": len(inputs.formations),
                "surface_point_count": len(inputs.surface_points),
                "orientation_count": len(inputs.orientations),
                "demo_ready": inputs.is_demo_ready(),
            },
        )
        summary: dict[str, Any] = {
            "succeeded": True,
            "demo_ready": inputs.is_demo_ready(),
            "extent": inputs.extent.model_dump(),
            "crs": inputs.crs,
            "grid_resolution": inputs.grid_resolution.model_dump(),
            **inputs.summary,
        }
        _LOG.info(
            "gempy_inputs_built",
            extra={
                "run_id": run_id,
                "formation_count": len(inputs.formations),
                "surface_point_count": len(inputs.surface_points),
                "orientation_count": len(inputs.orientations),
            },
        )
        return artifact, summary

    def _run_dsl_build(
        self,
        *,
        extraction: LLMExtraction,
        run_dir: Path,
    ) -> tuple[list[Artifact], dict[str, Any]]:
        """Phase 6: convert :class:`LLMExtraction` → DSL, parse + validate, persist.

        Failures here are non-fatal: the run still produces a fixture-derived
        mesh + field, but the manifest's ``dsl_summary`` records what went
        wrong so the demo UI can call attention to it.
        """

        try:
            dsl_text, build_report = build_dsl_from_extraction(extraction)
        except Exception as exc:
            _LOG.exception("dsl_build_failed", extra={"run_id": run_dir.name})
            return [], {
                "succeeded": False,
                "stage": "build",
                "error": str(exc),
            }

        artifacts: list[Artifact] = []
        dsl_path = run_dir / "geology.dsl"
        dsl_path.write_text(dsl_text, encoding="utf-8")
        artifacts.append(
            Artifact(
                kind="generated_dsl",
                filename=dsl_path.name,
                relative_path=dsl_path.name,
                url="",
                bytes=dsl_path.stat().st_size,
                media_type="text/plain",
                description=(
                    "DSL deterministically derived from the structured LLM extraction."
                ),
                metadata=dict(build_report.summary),
            )
        )

        try:
            program, validation = parse_and_validate(dsl_text)
        except Exception as exc:
            _LOG.exception("dsl_parse_failed", extra={"run_id": run_dir.name})
            return artifacts, {
                **build_report.summary,
                "succeeded": False,
                "stage": "parse",
                "error": str(exc),
                "warnings": list(build_report.warnings),
            }

        report_payload = _report_to_payload(validation)
        report_path = run_dir / "validation_report_generated.json"
        report_path.write_text(report_payload.model_dump_json(indent=2), encoding="utf-8")
        artifacts.append(
            Artifact(
                kind="generated_validation_report",
                filename=report_path.name,
                relative_path=report_path.name,
                url="",
                bytes=report_path.stat().st_size,
                media_type="application/json",
                description=(
                    "Structural validation of the LLM-derived DSL Program."
                ),
            )
        )

        summary: dict[str, Any] = {
            **build_report.summary,
            "succeeded": validation.is_valid,
            "stage": "validate" if validation.is_valid else "validate_failed",
            "rock_ids": [r.id for r in program.rocks],
            "deposition_ids": [d.id for d in program.depositions],
            "intrusion_ids": [i.id for i in program.intrusions],
            "warnings": list(build_report.warnings),
            "validation_error_count": len(validation.errors),
            "validation_warning_count": len(validation.warnings),
        }
        if not validation.is_valid:
            summary["validation_errors"] = [str(e) for e in validation.errors[:5]]

        _LOG.info(
            "dsl_build_completed",
            extra={
                "run_id": run_dir.name,
                "rock_count": len(program.rocks),
                "deposition_count": len(program.depositions),
                "is_valid": validation.is_valid,
            },
        )
        return artifacts, summary

    def _persist_ranked_chunks(
        self,
        *,
        document_id: str,
        ranked: RankedChunks,
        run_dir: Path,
    ) -> tuple[Artifact, dict[str, Any]]:
        """Persist a pre-computed :class:`RankedChunks` artifact under ``run_dir``."""

        dest = run_dir / "ranked_chunks.json"
        dest.write_text(ranked.model_dump_json(indent=2), encoding="utf-8")
        artifact = Artifact(
            kind="ranked_chunks",
            filename=dest.name,
            relative_path=dest.name,
            url="",  # surfaced via /api/run/{id}, not statically served
            bytes=dest.stat().st_size,
            media_type="application/json",
            description="Page-aware chunks with NYC-geology relevance scores.",
            metadata={
                "document_id": document_id,
                "chunk_count": ranked.chunk_count,
                "page_count": ranked.page_count,
            },
        )
        summary = {
            "document_id": document_id,
            "chunk_count": ranked.chunk_count,
            "page_count": ranked.page_count,
            "top_chunk_id": ranked.chunks[0].chunk_id if ranked.chunks else None,
            **ranked.summary,
        }
        _LOG.info(
            "ranked_chunks_written",
            extra={
                "run_id": run_dir.name,
                "document_id": document_id,
                "chunk_count": ranked.chunk_count,
            },
        )
        return artifact, summary

    def _fail_run(self, manifest: RunManifest, run_dir: Path, message: str) -> RunManifest:
        updated = manifest.model_copy(
            update={
                "status": RunStatus.FAILED,
                "updated_at": datetime.now(UTC),
                "error": message,
            }
        )
        try:
            (run_dir / "manifest.json").write_text(
                updated.model_dump_json(indent=2), encoding="utf-8"
            )
        except OSError:
            _LOG.exception("could_not_write_failure_manifest", extra={"run_id": updated.run_id})
        self._remember(
            RunState(run_id=updated.run_id, status=RunStatus.FAILED, manifest=updated)
        )
        _LOG.warning("run_failed", extra={"run_id": updated.run_id, "error": message})
        return updated

    def _remember(self, state: RunState) -> None:
        with self._lock:
            self._cache[state.run_id] = state

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def _purge_cache(self) -> None:
        """Clear the in-memory cache. Tests use this between cases."""
        with self._lock:
            self._cache.clear()


# ----------------------------------------------------------------------
# Helpers (module level so they are easy to unit-test)
# ----------------------------------------------------------------------


def _field_source_for_engine(engine: str) -> FieldSource:
    """Map a mesh engine name onto the field's source label.

    The two namespaces overlap (``rbf``, ``gempy``, ``synthetic``) but
    fields can also be ``stub``, which is not a valid mesh engine, so
    they're tracked as separate ``Literal``s in the schemas.
    """

    if engine == "gempy":
        return "gempy"
    if engine == "rbf":
        return "rbf"
    return "synthetic"


def _color_overrides_from_extraction(extraction: dict[str, Any]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for entry in extraction.get("rocks", []):
        if isinstance(entry, dict):
            rid = entry.get("id")
            color = entry.get("color_hex")
            if isinstance(rid, str) and isinstance(color, str):
                overrides[rid] = color
    return overrides


def _layer_summary(layers: list[LayerMesh], program: Program) -> list[dict[str, Any]]:
    rock_lookup = {r.id: r for r in program.rocks}
    out: list[dict[str, Any]] = []
    for layer in layers:
        # The synthetic surface_id matches the originating event id;
        # follow the event back to its rock.
        rock_id: str | None = None
        for ev in program.all_events:
            if ev.id == layer.surface_id and getattr(ev, "rock_id", None):
                rock_id = ev.rock_id
                break
        rock = rock_lookup.get(rock_id) if rock_id else None
        out.append(
            {
                "surface_id": layer.surface_id,
                "rock_id": rock_id,
                "name": layer.name,
                "rock_type": layer.rock_type,
                "color_hex": layer.color_hex,
                "vertex_count": layer.vertex_count(),
                "face_count": layer.face_count(),
                "approximate_age_ma": _age_ma(rock) if rock else None,
            }
        )
    return out


def _age_ma(rock: Any) -> float | None:
    age = getattr(rock, "approximate_age", None)
    if age is None:
        return None
    return getattr(age, "to_ma", lambda: None)()


def _format_report(report: ValidationReport) -> str:
    issues = "; ".join(str(e) for e in report.errors)
    return f"DSL validation failed: {issues}" if issues else "DSL validation failed."


def _report_to_payload(report: ValidationReport) -> ValidationReportPayload:
    error_issues = [
        ValidationIssue(
            severity="error",
            message=str(e),
            location=str(e.location) if getattr(e, "location", None) else None,
        )
        for e in report.errors
    ]
    warning_issues = [
        ValidationIssue(severity="warning", message=str(w), location=None)
        for w in report.warnings
    ]
    return ValidationReportPayload(
        is_valid=report.is_valid,
        error_count=len(error_issues),
        warning_count=len(warning_issues),
        errors=error_issues,
        warnings=warning_issues,
    )


# ----------------------------------------------------------------------
# Module-level accessor
# ----------------------------------------------------------------------


_DEFAULT_SERVICE: RunService | None = None


def get_run_service() -> RunService:
    """Return the process-wide :class:`RunService` singleton."""

    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = RunService()
    return _DEFAULT_SERVICE


def reset_run_service() -> None:
    """Drop the cached singleton (used by tests)."""

    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = None


__all__ = [
    "RunService",
    "get_run_service",
    "reset_run_service",
]
