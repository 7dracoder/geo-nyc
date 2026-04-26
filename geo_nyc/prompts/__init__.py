"""Versioned prompt templates loaded from ``.md`` files.

Each prompt file uses two delimited sections:

```
=== SYSTEM ===
<role + global rules>
=== USER ===
<task body, with {placeholders}>
```

Both sections are mandatory. Placeholder values are passed via
:meth:`PromptTemplate.render`; missing placeholders raise loudly so the
demo never silently ships an unrenderable prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

_PROMPT_DIR = Path(__file__).resolve().parent
_SYSTEM_MARK = "=== SYSTEM ==="
_USER_MARK = "=== USER ==="


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    system: str
    user: str

    def render(self, **kwargs: object) -> tuple[str, str]:
        """Return ``(system, user)`` with ``{placeholders}`` substituted."""

        try:
            user = Template(self.user).substitute(**kwargs)
        except KeyError as exc:
            raise KeyError(
                f"Prompt '{self.name}' is missing placeholder substitution: {exc}"
            ) from exc
        return self.system, user


def load_prompt(name: str) -> PromptTemplate:
    """Load a prompt by file stem (e.g. ``nyc_geology_extraction``)."""

    path = _PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    raw = path.read_text(encoding="utf-8")
    if _SYSTEM_MARK not in raw or _USER_MARK not in raw:
        raise ValueError(
            f"Prompt '{name}' must contain both '{_SYSTEM_MARK}' and '{_USER_MARK}' delimiters."
        )

    _, _, after_system = raw.partition(_SYSTEM_MARK)
    system_block, _, user_block = after_system.partition(_USER_MARK)
    system = system_block.strip()
    user = user_block.strip()
    if not system or not user:
        raise ValueError(f"Prompt '{name}' has an empty SYSTEM or USER section.")
    return PromptTemplate(name=name, system=system, user=user)


__all__ = ["PromptTemplate", "load_prompt"]
