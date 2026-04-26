"""NYC geology glossary + name normalisation.

The LLM emits formation names in whatever spelling the source PDF used
(``"manhattan schist"`` / ``"Manhattan Schist Formation"`` / ``"MnS"``).
The DSL builder needs *one* canonical spelling per formation so it can:

* deduplicate ``ROCK`` statements,
* generate stable identifiers,
* attach default ``rock_type`` values when the LLM left them ``null``,
* feed the mesh visualisation a colour palette.

This module owns that mapping. The default glossary is shipped under
``data/fixtures/nyc_geology_glossary.json`` and loaded lazily so tests
can inject a custom file without touching disk.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from geo_nyc.config import REPO_ROOT
from geo_nyc.exceptions import ConfigurationError

GlossaryRockType = Literal["sedimentary", "volcanic", "intrusive", "metamorphic"]

_GLOSSARY_PATH = REPO_ROOT / "data" / "fixtures" / "nyc_geology_glossary.json"
_NON_WORD = re.compile(r"[^\w]+")
_REPEAT_UNDERSCORE = re.compile(r"_+")


def _normalise_key(value: str) -> str:
    """Collapse a name to a comparison key (case- and whitespace-insensitive)."""

    cleaned = _REPEAT_UNDERSCORE.sub("_", _NON_WORD.sub("_", value.strip().lower()))
    return cleaned.strip("_")


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    """One canonical formation entry."""

    canonical: str
    rock_type: GlossaryRockType | None = None
    color_hex: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def all_keys(self) -> set[str]:
        keys = {_normalise_key(self.canonical)}
        keys.update(_normalise_key(a) for a in self.aliases if a.strip())
        return keys


class GeologyGlossary:
    """Read-only mapping from spelling → :class:`GlossaryEntry`."""

    def __init__(self, entries: list[GlossaryEntry]) -> None:
        self._entries: tuple[GlossaryEntry, ...] = tuple(entries)
        index: dict[str, GlossaryEntry] = {}
        for entry in self._entries:
            for key in entry.all_keys():
                # Last-wins on collisions; we log via ConfigurationError below
                # in :meth:`load` so the offender is obvious.
                index[key] = entry
        self._index = index

    @property
    def entries(self) -> tuple[GlossaryEntry, ...]:
        return self._entries

    def lookup(self, name: str) -> GlossaryEntry | None:
        if not name:
            return None
        return self._index.get(_normalise_key(name))

    def canonical(self, name: str) -> str:
        """Return the canonical spelling, or the trimmed input if unknown."""

        entry = self.lookup(name)
        if entry is not None:
            return entry.canonical
        return name.strip()

    def rock_type(self, name: str) -> GlossaryRockType | None:
        entry = self.lookup(name)
        return entry.rock_type if entry else None

    def color_for(self, name: str) -> str | None:
        entry = self.lookup(name)
        return entry.color_hex if entry else None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> GeologyGlossary:
        if not path.is_file():
            raise ConfigurationError(f"Geology glossary not found at {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Glossary {path} is not valid JSON: {exc}") from exc

        formations = payload.get("formations")
        if not isinstance(formations, list):
            raise ConfigurationError(
                f"Glossary {path} must define a top-level 'formations' list."
            )

        entries: list[GlossaryEntry] = []
        seen_keys: dict[str, str] = {}
        for raw in formations:
            canonical = (raw.get("canonical") or "").strip()
            if not canonical:
                raise ConfigurationError(
                    f"Glossary {path} contains an entry without a 'canonical' name: {raw!r}"
                )
            rock_type = raw.get("rock_type")
            if rock_type is not None and rock_type not in {
                "sedimentary",
                "volcanic",
                "intrusive",
                "metamorphic",
            }:
                raise ConfigurationError(
                    f"Glossary entry {canonical!r} has invalid rock_type {rock_type!r}."
                )
            aliases = tuple(
                str(a).strip() for a in (raw.get("aliases") or []) if str(a).strip()
            )
            entry = GlossaryEntry(
                canonical=canonical,
                rock_type=rock_type,
                color_hex=raw.get("color_hex"),
                aliases=aliases,
            )
            for key in entry.all_keys():
                if key in seen_keys and seen_keys[key] != canonical:
                    raise ConfigurationError(
                        f"Glossary {path} has duplicate alias {key!r} shared by "
                        f"{seen_keys[key]!r} and {canonical!r}."
                    )
                seen_keys[key] = canonical
            entries.append(entry)

        return cls(entries)


@lru_cache(maxsize=1)
def default_glossary() -> GeologyGlossary:
    """Return the shipped NYC glossary (cached)."""

    return GeologyGlossary.load(_GLOSSARY_PATH)


def reset_default_glossary() -> None:
    """Clear the cached glossary -- useful when tests swap the file out."""

    default_glossary.cache_clear()


__all__ = [
    "GeologyGlossary",
    "GlossaryEntry",
    "default_glossary",
    "reset_default_glossary",
]
