"""End-to-end smoke test for a deployed geo-nyc API.

Usage::

    python scripts/smoke_render.py https://geo-nyc-api.onrender.com

Hits, in order:

1. ``GET  /api/health``        — backend is alive
2. ``GET  /api/llm/health``    — Groq (or Ollama) provider is reachable
3. ``GET  /api/runs``          — list endpoint responds
4. ``POST /api/run``           — fixture-mode run completes
5. ``POST /api/dsl/parse``     — DSL parser accepts the demo DSL
6. ``POST /api/run`` (inline)  — inline-DSL run completes

Exits 0 if everything is green, non-zero otherwise. Prints a compact
report so it's safe to wire into CI / a deploy hook.

This script intentionally has *zero* third-party deps (uses urllib +
json from the stdlib) so it runs on any Python 3.9+ environment,
including a fresh Render shell with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEMO_DSL = """\
ROCK R_FILL [ name: "Anthropogenic Fill"; type: sedimentary ]
ROCK R_TILL [ name: "Glacial Till"; type: sedimentary; age: 0.02Ma ]
ROCK R_OUTWASH [ name: "Glacial Outwash"; type: sedimentary; age: 0.018Ma ]
ROCK R_SCHIST [ name: "Manhattan Schist"; type: metamorphic; age: 450Ma ]

DEPOSITION D_SCHIST [ rock: R_SCHIST; time: 450Ma ]
DEPOSITION D_TILL [ rock: R_TILL; time: 0.02Ma; after: E_GLACIAL ]
DEPOSITION D_OUTWASH [ rock: R_OUTWASH; time: 0.018Ma; after: D_TILL ]
DEPOSITION D_FILL [ rock: R_FILL; after: D_OUTWASH ]
EROSION E_GLACIAL [ after: D_SCHIST ]
"""


class StepFailed(RuntimeError):
    """Raised when a smoke-test step fails so we can collect, not abort."""


def _request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 90.0,
) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8") or "{}"
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def _step(name: str, fn) -> tuple[bool, str]:
    started = time.monotonic()
    try:
        detail = fn()
    except StepFailed as exc:
        elapsed = time.monotonic() - started
        return False, f"FAIL  {name:<30} ({elapsed:5.1f}s) — {exc}"
    except Exception as exc:  # noqa: BLE001 — smoke test catches everything
        elapsed = time.monotonic() - started
        return False, f"ERROR {name:<30} ({elapsed:5.1f}s) — {exc!r}"
    elapsed = time.monotonic() - started
    suffix = f" — {detail}" if detail else ""
    return True, f"PASS  {name:<30} ({elapsed:5.1f}s){suffix}"


def check_health(base: str) -> str:
    status, body = _request("GET", f"{base}/api/health")
    if status != 200 or not isinstance(body, dict) or body.get("status") != "ok":
        raise StepFailed(f"status={status} body={body!r}")
    return f"version={body.get('version')} fixtures={body.get('use_fixtures')}"


def check_llm_health(base: str) -> str:
    status, body = _request("GET", f"{base}/api/llm/health")
    if status != 200 or not isinstance(body, dict):
        raise StepFailed(f"status={status} body={body!r}")
    if body.get("status") != "ok":
        raise StepFailed(f"llm not ok: {body}")
    return f"provider={body.get('provider')} model={body.get('model')}"


def check_runs_list(base: str) -> str:
    status, body = _request("GET", f"{base}/api/runs")
    if status != 200:
        raise StepFailed(f"status={status} body={body!r}")
    runs = body if isinstance(body, list) else body.get("runs") if isinstance(body, dict) else None
    if not isinstance(runs, list):
        raise StepFailed(f"unexpected shape: {body!r}")
    return f"{len(runs)} run(s) currently visible"


def check_fixture_run(base: str) -> str:
    status, body = _request("POST", f"{base}/api/run", payload={}, timeout=180)
    if status not in (200, 201):
        raise StepFailed(f"status={status} body={body!r}")
    if not isinstance(body, dict):
        raise StepFailed(f"unexpected shape: {body!r}")
    rid = body.get("run_id") or body.get("id") or (body.get("manifest") or {}).get("run_id")
    state = body.get("status") or body.get("state") or (body.get("manifest") or {}).get("status")
    if not rid:
        raise StepFailed(f"no run_id: {body!r}")
    return f"run_id={rid} status={state}"


def check_dsl_parse(base: str) -> str:
    status, body = _request("POST", f"{base}/api/dsl/parse", payload={"text": DEMO_DSL})
    if status != 200 or not isinstance(body, dict):
        raise StepFailed(f"status={status} body={body!r}")
    if not body.get("is_valid"):
        raise StepFailed(f"DSL rejected: {body!r}")
    return (
        f"rocks={body.get('rocks_count')} "
        f"depositions={body.get('depositions_count')} "
        f"erosions={body.get('erosions_count')}"
    )


def check_inline_dsl_run(base: str) -> str:
    status, body = _request(
        "POST",
        f"{base}/api/run",
        payload={"dsl_text": DEMO_DSL},
        timeout=180,
    )
    if status not in (200, 201):
        raise StepFailed(f"status={status} body={body!r}")
    if not isinstance(body, dict):
        raise StepFailed(f"unexpected shape: {body!r}")
    rid = body.get("run_id") or body.get("id") or (body.get("manifest") or {}).get("run_id")
    if not rid:
        raise StepFailed(f"no run_id: {body!r}")
    mode = (body.get("manifest") or {}).get("mode") or body.get("mode")
    return f"run_id={rid} mode={mode}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "base_url",
        help="Public base URL of the deployed API, e.g. https://geo-nyc-api.onrender.com",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print(f"Smoke-testing geo-nyc backend at {base}")
    print("=" * 70)

    steps: list[tuple[str, callable]] = [
        ("health", lambda: check_health(base)),
        ("llm/health", lambda: check_llm_health(base)),
        ("runs list", lambda: check_runs_list(base)),
        ("fixture run", lambda: check_fixture_run(base)),
        ("dsl/parse", lambda: check_dsl_parse(base)),
        ("inline-DSL run", lambda: check_inline_dsl_run(base)),
    ]

    results: list[tuple[bool, str]] = []
    for name, fn in steps:
        ok, line = _step(name, fn)
        print(line, flush=True)
        results.append((ok, line))

    print("=" * 70)
    failures = [line for ok, line in results if not ok]
    if failures:
        print(f"FAILED ({len(failures)}/{len(results)})")
        return 1
    print(f"OK ({len(results)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
