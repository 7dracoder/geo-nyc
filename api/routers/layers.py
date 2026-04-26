"""Layer manifest endpoint.

Reads ``manifest.json`` written by the Part 3 data scripts
(``geonyc-data/scripts/fetch_open_data.py`` and ``build_field.py``) and
returns the parsed ``layers`` array. The frontend consumes this to
drive overlay rendering on the MapLibre map.

Source of truth for paths is :class:`geo_nyc.config.Settings`
(``GEO_NYC_DATA_LAYER_DIR``); a static fallback under
``geonyc-data/web/public/layers/`` keeps the historical Part 3 layout
working without configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

from geo_nyc.config import REPO_ROOT, get_settings

router = APIRouter(tags=["layers"])

# Fallback used only if the configured data-layer dir doesn't yet have
# a manifest.json (e.g. fresh checkout, scripts not run). Matches the
# legacy Part 3 stub mirror.
_LEGACY_FALLBACK = REPO_ROOT / "geonyc-data" / "web" / "public" / "layers" / "manifest.json"


def _resolve_manifest_path() -> Path:
    settings = get_settings()
    primary = settings.data_layer_layers_dir / "manifest.json"
    if primary.exists():
        return primary
    if _LEGACY_FALLBACK.exists():
        return _LEGACY_FALLBACK
    return primary  # surfaces a clean 404 below


@router.get("/layers")
def get_layers() -> dict[str, list[dict[str, Any]]]:
    manifest_path = _resolve_manifest_path()
    if not manifest_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Layer manifest not found at {manifest_path}. "
                "Run `python geonyc-data/scripts/fetch_open_data.py` first."
            ),
        )

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Layer manifest is not valid JSON: {exc}",
        ) from exc

    layers = payload.get("layers")
    if not isinstance(layers, list):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Layer manifest is invalid: expected top-level `layers` list.",
        )
    return {"layers": layers}
