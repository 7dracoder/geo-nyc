"""Seed the backend with the 3 canonical NYC geology PDFs and run them.

This is the operator-side glue between the source PDFs declared in
``geonyc-data/.../source_pdfs/sources.json`` and the run pipeline:

1. Make sure each PDF is on disk under ``GEO_NYC_DATA_LAYER_DIR`` —
   downloading it from ``source_url`` if missing.
2. Upload + extract each PDF via :class:`DocumentService` so the
   ``/api/documents`` listing reflects them.
3. Trigger one ``/api/run`` per PDF with ``use_llm=true`` so the
   PDF-derived DSL drives the mesh + GemPy inputs (the run service
   degrades to ``document_chunks+fixture`` automatically when Ollama is
   unavailable, so the script never breaks the demo).

Run as::

    python -m geo_nyc.runs.bootstrap

Or via Make::

    make seed-runs

The script is idempotent — re-running it with the PDFs already on disk
just re-creates fresh ``/api/run`` entries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from geo_nyc.config import Settings, get_settings
from geo_nyc.documents import DocumentService, get_document_service
from geo_nyc.exceptions import GeoNYCError
from geo_nyc.logging import configure_logging, get_logger
from geo_nyc.runs.run_service import RunService, get_run_service

_LOG = get_logger(__name__)
_USER_AGENT = "geo-nyc-bootstrap/1.0 (+https://github.com/7dracoder/geo-nyc)"
_DOWNLOAD_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class _PdfSource:
    """One row from ``sources.json`` after path resolution."""

    id: str
    title: str
    local_path: Path
    source_url: str


def _load_sources(settings: Settings) -> list[_PdfSource]:
    """Resolve ``sources.json`` against ``GEO_NYC_DATA_LAYER_DIR``."""

    candidates = [
        settings.data_layer_dir / "source_pdfs" / "sources.json",
        settings.data_layer_dir.parent / "data" / "source_pdfs" / "sources.json",
    ]
    sources_path: Path | None = next((p for p in candidates if p.is_file()), None)
    if sources_path is None:
        raise GeoNYCError(
            "Cannot find sources.json. Expected one of: "
            + ", ".join(str(c) for c in candidates)
        )

    payload = json.loads(sources_path.read_text(encoding="utf-8"))
    documents = payload.get("documents", [])
    if not documents:
        raise GeoNYCError(f"sources.json at {sources_path} has no documents.")

    out: list[_PdfSource] = []
    repo_root = sources_path.parent.parent.parent  # geonyc-data/genyc_data/source_pdfs/.. -> geonyc-data
    for doc in documents:
        local = doc.get("local_path", "")
        path = Path(local) if local else Path()
        if not path.is_absolute():
            # Anchor relative paths under the geonyc-data root (where
            # sources.json lives), not the repo root, so the layout in
            # the JSON matches what we read on disk.
            path = (repo_root / local).resolve()
        out.append(
            _PdfSource(
                id=str(doc["id"]),
                title=str(doc.get("title", doc["id"])),
                local_path=path,
                source_url=str(doc["source_url"]),
            )
        )
    return out


def _download_pdf(source: _PdfSource) -> bool:
    """Best-effort download of ``source`` to ``source.local_path``.

    Returns True on success, False on any network/HTTP failure (the
    caller falls back to "skip this PDF" rather than aborting the
    whole bootstrap).
    """

    if source.local_path.is_file() and source.local_path.stat().st_size > 0:
        return True
    source.local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = Request(source.source_url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
            content = resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        _LOG.warning(
            "bootstrap_download_failed",
            extra={"id": source.id, "url": source.source_url, "error": str(exc)},
        )
        return False

    if not content.startswith(b"%PDF-"):
        _LOG.warning(
            "bootstrap_download_not_pdf",
            extra={"id": source.id, "url": source.source_url, "head": content[:8]},
        )
        return False
    source.local_path.write_bytes(content)
    return True


def _ingest_pdf(source: _PdfSource, *, service: DocumentService) -> str | None:
    """Upload + extract one PDF; return its ``document_id`` or ``None``."""

    try:
        content = source.local_path.read_bytes()
    except OSError as exc:
        _LOG.warning(
            "bootstrap_read_failed",
            extra={"id": source.id, "path": str(source.local_path), "error": str(exc)},
        )
        return None

    record = service.upload(
        content=content,
        filename=source.local_path.name,
        media_type="application/pdf",
    )
    try:
        service.extract(record.document_id)
    except GeoNYCError as exc:
        _LOG.warning(
            "bootstrap_extract_failed",
            extra={"id": source.id, "document_id": record.document_id, "error": str(exc)},
        )
        return record.document_id
    return record.document_id


async def _run_one(
    *,
    runs: RunService,
    source: _PdfSource,
    document_id: str,
    use_llm: bool,
    fixture_name: str,
) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "document_id": document_id,
        "use_llm": use_llm,
    }
    try:
        manifest = await runs.acreate_run(
            request_payload=request_payload,
            fixture_name=fixture_name,
        )
    except GeoNYCError as exc:
        _LOG.error(
            "bootstrap_run_failed",
            extra={"id": source.id, "document_id": document_id, "error": str(exc)},
        )
        return {
            "id": source.id,
            "document_id": document_id,
            "status": "failed",
            "error": str(exc),
        }
    return {
        "id": source.id,
        "document_id": document_id,
        "run_id": manifest.run_id,
        "mode": manifest.mode,
        "status": manifest.status.value,
    }


async def _run_async(
    *,
    settings: Settings,
    documents: DocumentService,
    runs: RunService,
    use_llm: bool,
    fixture_name: str,
    only: list[str] | None,
) -> list[dict[str, Any]]:
    sources = _load_sources(settings)
    if only:
        keep = {s.lower() for s in only}
        sources = [s for s in sources if s.id.lower() in keep]
        if not sources:
            raise GeoNYCError(
                f"--only filter {only!r} matched no PDFs in sources.json."
            )

    summaries: list[dict[str, Any]] = []
    for source in sources:
        downloaded = _download_pdf(source)
        if not downloaded:
            summaries.append(
                {"id": source.id, "status": "skipped", "reason": "download_failed"}
            )
            continue
        document_id = _ingest_pdf(source, service=documents)
        if document_id is None:
            summaries.append(
                {"id": source.id, "status": "skipped", "reason": "ingest_failed"}
            )
            continue
        summary = await _run_one(
            runs=runs,
            source=source,
            document_id=document_id,
            use_llm=use_llm,
            fixture_name=fixture_name,
        )
        summaries.append(summary)
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed geo-nyc with the canonical NYC geology PDFs.")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the Ollama LLM extraction step (chunks-only).",
    )
    parser.add_argument(
        "--fixture",
        default="nyc_demo",
        help="Fixture bundle to use for extent + scaffolding (default: nyc_demo).",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Restrict to one or more PDF ids from sources.json (e.g. usgs_i2306).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_directories()

    summaries = asyncio.run(
        _run_async(
            settings=settings,
            documents=get_document_service(),
            runs=get_run_service(),
            use_llm=not args.no_llm,
            fixture_name=args.fixture,
            only=args.only,
        )
    )
    print(json.dumps({"results": summaries}, indent=2))
    return 0 if any(s.get("run_id") for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
