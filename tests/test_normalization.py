"""Unit tests for :mod:`geo_nyc.domain.normalization`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from geo_nyc.domain.normalization import (
    GeologyGlossary,
    default_glossary,
    reset_default_glossary,
)
from geo_nyc.exceptions import ConfigurationError


def test_default_glossary_loads_known_formations() -> None:
    reset_default_glossary()
    glossary = default_glossary()
    canonical = {entry.canonical for entry in glossary.entries}
    for required in {
        "Manhattan Schist",
        "Inwood Marble",
        "Fordham Gneiss",
        "Walloomsac Formation",
        "Hartland Formation",
        "Ravenswood Granodiorite",
    }:
        assert required in canonical


def test_canonical_lookup_is_case_and_whitespace_insensitive() -> None:
    glossary = default_glossary()
    assert glossary.canonical("manhattan schist") == "Manhattan Schist"
    assert glossary.canonical("  Manhattan SCHIST  ") == "Manhattan Schist"
    assert glossary.canonical("MnS") == "Manhattan Schist"
    assert glossary.canonical("Hartland-Manhattan schist") == "Manhattan Schist"


def test_unknown_name_returned_unchanged() -> None:
    glossary = default_glossary()
    assert glossary.canonical("Some Unknown Formation") == "Some Unknown Formation"
    assert glossary.lookup("Some Unknown Formation") is None
    assert glossary.rock_type("Some Unknown Formation") is None


def test_rock_type_and_color_for_known_entries() -> None:
    glossary = default_glossary()
    assert glossary.rock_type("manhattan schist") == "metamorphic"
    assert glossary.rock_type("Ravenswood pluton") == "intrusive"
    assert glossary.color_for("Inwood Marble") == "#D7CDC4"


def test_load_rejects_duplicate_aliases(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "formations": [
                    {
                        "canonical": "A",
                        "rock_type": "metamorphic",
                        "aliases": ["shared"],
                    },
                    {
                        "canonical": "B",
                        "rock_type": "metamorphic",
                        "aliases": ["shared"],
                    },
                ]
            }
        )
    )
    with pytest.raises(ConfigurationError, match="duplicate alias"):
        GeologyGlossary.load(bad)


def test_load_rejects_invalid_rock_type(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "formations": [
                    {"canonical": "Foo", "rock_type": "purple", "aliases": []}
                ]
            }
        )
    )
    with pytest.raises(ConfigurationError, match="invalid rock_type"):
        GeologyGlossary.load(bad)


def test_load_rejects_missing_canonical(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"formations": [{"rock_type": "metamorphic"}]})
    )
    with pytest.raises(ConfigurationError, match="without a 'canonical'"):
        GeologyGlossary.load(bad)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        GeologyGlossary.load(tmp_path / "missing.json")
