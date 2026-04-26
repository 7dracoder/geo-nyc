"""Unit tests for :mod:`geo_nyc.prompts` and the LLM repair flow.

Covers Phase 12 — "repair prompt creation". The extraction prompt and
the repair prompt are the contract between :class:`LLMExtractor` and
the local Ollama model; if either fails to render — or silently drops a
placeholder — the LLM either hallucinates wildly or returns malformed
output. The tests below pin both behaviours.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geo_nyc.extraction.llm_extractor import LLMExtractor
from geo_nyc.extraction.structured import StructuredValidationReport
from geo_nyc.prompts import PromptTemplate, load_prompt


def test_load_extraction_prompt_returns_both_sections() -> None:
    prompt = load_prompt("nyc_geology_extraction")
    assert prompt.name == "nyc_geology_extraction"
    assert prompt.system, "system block must not be empty"
    assert prompt.user, "user block must not be empty"
    # Sanity: the schema is the bedrock of this prompt; if it's gone we
    # silently regress LLM output quality.
    assert "JSON schema" in prompt.system or "JSON schema" in prompt.user
    assert "$document_id" in prompt.user
    assert "$chunks_block" in prompt.user


def test_load_repair_prompt_has_required_placeholders() -> None:
    prompt = load_prompt("repair_extraction")
    # The repair prompt MUST reference every placeholder LLMExtractor
    # supplies. Missing any of these would make the prompt template
    # raise at runtime, breaking the demo's repair loop.
    for placeholder in (
        "$document_id",
        "$chunks_block",
        "$previous_json",
        "$errors_block",
    ):
        assert placeholder in prompt.user, f"repair prompt missing {placeholder!r}"

    # Repair semantics: "fix only" / "do not invent" must remain in
    # the system block — these are the rails that keep retries from
    # hallucinating new content.
    assert "Fix ONLY" in prompt.system
    assert "Do NOT invent" in prompt.system


def test_load_unknown_prompt_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist")


def test_load_rejects_prompt_without_delimiters(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "broken.md"
    bad.write_text("just a plain paragraph", encoding="utf-8")
    # Point the loader at a temp directory by patching the resolved
    # _PROMPT_DIR. We do this directly rather than via env vars to
    # exercise the production error path.
    import geo_nyc.prompts as prompt_module

    monkeypatch.setattr(prompt_module, "_PROMPT_DIR", tmp_path)
    with pytest.raises(ValueError, match="must contain both"):
        prompt_module.load_prompt("broken")


def test_load_rejects_empty_section(tmp_path, monkeypatch) -> None:
    half = tmp_path / "half.md"
    half.write_text("=== SYSTEM ===\n=== USER ===\nbody only", encoding="utf-8")
    import geo_nyc.prompts as prompt_module

    monkeypatch.setattr(prompt_module, "_PROMPT_DIR", tmp_path)
    with pytest.raises(ValueError, match="empty SYSTEM or USER"):
        prompt_module.load_prompt("half")


def test_render_substitutes_placeholders() -> None:
    template = PromptTemplate(
        name="t",
        system="role text",
        user="hello $name, see chunk $chunk_id",
    )
    system, user = template.render(name="Cursor", chunk_id="C1")
    assert system == "role text"
    assert user == "hello Cursor, see chunk C1"


def test_render_raises_keyerror_on_missing_placeholder() -> None:
    template = PromptTemplate(
        name="t",
        system="role",
        user="hello $name",
    )
    with pytest.raises(KeyError, match="missing placeholder substitution"):
        template.render()


def test_render_does_not_eat_dollar_signs_in_data() -> None:
    """Pricing / inline maths shouldn't get re-substituted."""

    template = PromptTemplate(
        name="t",
        system="role",
        user="cost: $$5.00 for $name",
    )
    _, user = template.render(name="abc")
    # ``$$`` is the escape for a literal ``$`` in string.Template.
    assert user == "cost: $5.00 for abc"


def test_repair_prompt_renders_with_extractor_inputs() -> None:
    """End-to-end: format an error report and feed it through the prompt.

    This is the *exact* code path :class:`LLMExtractor` walks during a
    repair attempt, minus the network round-trip. If either side
    drifts (placeholder names, error formatter shape) this test breaks
    loudly.
    """

    prompt = load_prompt("repair_extraction")

    report = StructuredValidationReport(
        is_valid=False,
        meets_demo_minimum=False,
        errors=[
            "formations[0].name: missing required field",
            "contacts[1].depth_value: 999 ft is suspiciously deep",
        ],
        warnings=["only one formation present"],
    )
    errors_block = LLMExtractor._format_errors(report)
    assert errors_block.startswith("- ") or "missing required field" in errors_block

    system, user = prompt.render(
        document_id="doc_abc123",
        chunks_block="[chunk_id=C1] Manhattan Schist outcrops at -22 m.",
        previous_json='{"formations": []}',
        errors_block=errors_block,
    )

    assert system == prompt.system
    assert "doc_abc123" in user
    assert "Manhattan Schist outcrops" in user
    assert '"formations": []' in user
    # Validation errors must surface inside the rendered USER block so
    # the LLM can act on them.
    assert "missing required field" in user
    assert "999 ft is suspiciously deep" in user


def test_repair_prompt_format_errors_handles_missing_validation() -> None:
    """When validation==None the previous attempt didn't even parse as
    JSON. The repair prompt MUST still produce a non-empty errors_block
    so :class:`PromptTemplate.render` doesn't raise on a missing key.
    """

    formatted = LLMExtractor._format_errors(None)
    assert formatted
    # Tell the model what actually went wrong (parse failure) — this
    # is the lever that makes attempt #2 try again with valid JSON.
    assert "json" in formatted.lower()


def test_extraction_and_repair_prompts_are_singletons_on_disk() -> None:
    """Catch accidental duplicate / shadow prompt files in the package."""

    prompts_dir = Path(__file__).resolve().parents[1] / "geo_nyc" / "prompts"
    md_files = sorted(p.name for p in prompts_dir.glob("*.md"))
    assert md_files == ["nyc_geology_extraction.md", "repair_extraction.md"]
