"""Fixture loader for ``GEO_NYC_USE_FIXTURES=true`` runs.

Reads the canonical demo fixture (``data/fixtures/nyc_demo/``) and
returns the pieces the run service needs:

* the structured extraction JSON (preserved verbatim for the manifest);
* the DSL source text (parsed + validated by the run service);
* the :class:`ModelExtent` derived from the fixture metadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geo_nyc.exceptions import ConfigurationError
from geo_nyc.modeling.extent import ModelExtent

_FIXTURE_DIRNAME = "nyc_demo"


@dataclass(frozen=True, slots=True)
class FixtureBundle:
    """Everything the run service needs from a fixture set."""

    name: str
    extraction: dict[str, Any]
    dsl_text: str
    extent: ModelExtent
    crs: str


def load_fixture_bundle(fixtures_dir: Path, name: str = _FIXTURE_DIRNAME) -> FixtureBundle:
    root = Path(fixtures_dir) / name
    if not root.is_dir():
        raise ConfigurationError(
            f"Fixture directory not found: {root} (configure GEO_NYC_FIXTURES_DIR or "
            f"create the directory)."
        )

    extraction_path = root / "extraction.json"
    dsl_path = root / "dsl.txt"
    if not extraction_path.is_file():
        raise ConfigurationError(f"Missing fixture file: {extraction_path}")
    if not dsl_path.is_file():
        raise ConfigurationError(f"Missing fixture file: {dsl_path}")

    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    dsl_text = dsl_path.read_text(encoding="utf-8")

    extent, crs = _extent_from_extraction(extraction)
    return FixtureBundle(
        name=name,
        extraction=extraction,
        dsl_text=dsl_text,
        extent=extent,
        crs=crs,
    )


def _extent_from_extraction(extraction: dict[str, Any]) -> tuple[ModelExtent, str]:
    site = extraction.get("site")
    if not isinstance(site, dict):
        raise ConfigurationError("Fixture extraction missing 'site' object.")

    bbox = site.get("bbox_xy_m") or {}
    depth = site.get("depth_range_m") or {}
    crs = site.get("crs") or "EPSG:32618"

    try:
        extent = ModelExtent(
            x_min=float(bbox["x_min"]),
            x_max=float(bbox["x_max"]),
            y_min=float(bbox["y_min"]),
            y_max=float(bbox["y_max"]),
            z_min=float(depth["z_min"]),
            z_max=float(depth["z_max"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"Fixture site extent is malformed: {exc}"
        ) from exc
    return extent, str(crs)


__all__ = ["FixtureBundle", "load_fixture_bundle"]
