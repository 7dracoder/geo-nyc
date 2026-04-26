"""Domain knowledge: NYC geology glossary, normalisation helpers."""

from geo_nyc.domain.normalization import (
    GeologyGlossary,
    GlossaryEntry,
    default_glossary,
    reset_default_glossary,
)

__all__ = [
    "GeologyGlossary",
    "GlossaryEntry",
    "default_glossary",
    "reset_default_glossary",
]
