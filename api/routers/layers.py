from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api", tags=["layers"])

REPO_ROOT = Path(__file__).resolve().parents[2]
PRIMARY_MANIFEST_PATH = REPO_ROOT / "genyc_data" / "layers" / "manifest.json"
FALLBACK_MANIFEST_PATH = REPO_ROOT / "web" / "public" / "layers" / "manifest.json"


def _resolve_manifest_path() -> Path:
    if PRIMARY_MANIFEST_PATH.exists():
        return PRIMARY_MANIFEST_PATH
    return FALLBACK_MANIFEST_PATH


@router.get("/layers")
def get_layers() -> dict[str, list[dict[str, Any]]]:
    manifest_path = _resolve_manifest_path()
    if not manifest_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Layer manifest not found at {PRIMARY_MANIFEST_PATH} or {FALLBACK_MANIFEST_PATH}",
        )

    with manifest_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if "layers" not in payload or not isinstance(payload["layers"], list):
        raise HTTPException(
            status_code=500,
            detail="Layer manifest is invalid: expected a top-level `layers` list.",
        )
    return {"layers": payload["layers"]}
