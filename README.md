# geo-nyc — Urban Subsurface AI (Backend)

Local, edge-computed FastAPI backend for **Urban Subsurface AI**: it
turns dense USGS NYC geological PDFs into a 3D subsurface model
(`.glb` + depth-to-bedrock scalar field) using a **local Ollama LLM**
and `scipy`/`gempy` modeling — **no cloud APIs** required.

This README is the operational guide for the backend repo (Part 2 of
the workstream split). The full project blueprint, frontend strategy,
and team coordination notes live in [`Project Blueprint`](#project-blueprint)
at the bottom.

---

## Table of contents

1. [Architecture in 30 seconds](#architecture-in-30-seconds)
2. [Quick start](#quick-start)
3. [Running the demo](#running-the-demo)
4. [Deploying with the Vercel frontend](#deploying-with-the-vercel-frontend)
5. [API contracts](#api-contracts)
6. [Static asset URLs](#static-asset-urls)
7. [Field grid schema](#field-grid-schema)
8. [Environment variables](#environment-variables)
9. [Manual smoke checklist](#manual-smoke-checklist)
10. [Tests, lint, and CI](#tests-lint-and-ci)
11. [Repository layout](#repository-layout)
12. [Troubleshooting](#troubleshooting)
13. [Project blueprint](#project-blueprint)
14. [License](#license)

---

## Architecture in 30 seconds

```
                       ┌──────────────────────────────────────────┐
                       │              FastAPI (api/)              │
PDF upload ─────────▶ │  /api/documents/*  /api/run  /api/runs   │
                       │  /api/health       /api/llm/health       │
                       │  /static/exports/* /static/fields/*      │
                       └────────┬───────────────────────┬─────────┘
                                │                       │
                ┌───────────────▼─────────┐   ┌─────────▼──────────┐
                │  geo_nyc.documents      │   │  geo_nyc.runs       │
                │  • PyMuPDF text extract │   │  • RunService        │
                │  • content-hash IDs     │   │  • Ranked chunks     │
                │  • per-page JSON store  │   │  • LLM extraction    │
                └─────────────┬───────────┘   │  • DSL build         │
                              │               │  • GemPy constraints │
                              ▼               │  • Mesh + field      │
                ┌─────────────────────────┐   │  • Manifest writer   │
                │  geo_nyc.ai (Ollama)    │   └──────────┬──────────┘
                │  • httpx, JSON mode     │              │
                │  • repair loop          │              │
                └─────────────────────────┘              │
                                                         ▼
                          ┌────────────────────────────────────────┐
                          │  geo_nyc.modeling                      │
                          │  • RBFRunner, GemPyRunner, Synthetic   │
                          │  • field_builder (RBF / mesh resample) │
                          │  • mesh_export (.glb), field_export    │
                          └────────────────────────────────────────┘
```

Every run writes a self-describing `manifest.json` plus mesh, field,
DSL, extraction, and validation artifacts under
`data/runs/{run_id}/`. The frontend fetches them by absolute URL,
stamped from `GEO_NYC_PUBLIC_BASE_URL`.

---

## Quick start

### Prerequisites

- **macOS / Linux** (M-series Mac is the reference platform).
- **Python 3.12** (3.13 is *not* supported — `pyproject.toml` pins
  `>=3.12,<3.13` because of GemPy / scientific wheels).
- **[Ollama](https://ollama.com)** running locally on
  `http://localhost:11434`.
- (Optional) **GemPy** for full implicit modeling. The default
  `RBFRunner` is the always-available fallback, so you can ship a
  demo without GemPy.
- (Optional, for live demo with Vercel) **ngrok** or
  **cloudflared** to expose the laptop API to the internet.

### 1. Install Python 3.12 + Ollama

```bash
# Homebrew (recommended on macOS)
brew install python@3.12 ollama
```

Start the Ollama daemon (in its own shell or as a background service):

```bash
ollama serve
```

Pull the demo model **once**:

```bash
ollama pull llama3.1:8b
```

Sanity-check Ollama is up:

```bash
curl -s http://localhost:11434/api/tags | jq .
```

### 2. Clone, venv, install

```bash
git clone https://github.com/somaditya/geo-nyc.git
cd geo-nyc

python3.12 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -e ".[dev]"
```

To enable the *real* GemPy modeling path (optional), also install:

```bash
pip install -e ".[modeling]"
```

`RBFRunner` is the default mesh engine and works without GemPy. The
service detects whichever runners are importable and records the
chosen one in the run manifest.

### 3. Configure environment

Copy the template and edit as needed:

```bash
cp .env.example .env
```

The defaults work for **local development** out of the box:

```bash
GEO_NYC_OLLAMA_BASE_URL=http://localhost:11434
GEO_NYC_OLLAMA_MODEL=llama3.1:8b
GEO_NYC_USE_FIXTURES=true
GEO_NYC_API_HOST=127.0.0.1
GEO_NYC_API_PORT=8000
GEO_NYC_PUBLIC_BASE_URL=http://localhost:8000
```

See [Environment variables](#environment-variables) for the full list,
and [Deploying with the Vercel frontend](#deploying-with-the-vercel-frontend)
for the values to change when running behind a tunnel.

### 4. Run the server

Two equivalent ways:

```bash
# Console script (installed by pyproject.toml)
geo-nyc

# Or, classically:
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

OpenAPI / Swagger UI: <http://localhost:8000/docs>.

### 5. Smoke test

```bash
curl -s http://localhost:8000/api/health      | jq .
curl -s http://localhost:8000/api/llm/health  | jq .
```

Both should return `"status": "ok"`. If `llm/health` says `"down"`,
[Ollama is not reachable](#ollama-not-reachable).

---

## Running the demo

### Fixture mode (offline, deterministic, ~1 second)

This is what `/api/run` does by default — no PDF, no LLM, no GemPy.
It exists so the demo always finishes, and so Part 1 (frontend) and
Part 3 (GIS/optimizer) can integrate against a stable artifact set.

```bash
curl -s -X POST http://localhost:8000/api/run \
  -H "content-type: application/json" \
  -d '{}' | jq '.run_id, .artifacts | length, .mesh_summary, .field_summary'
```

You should see something like:

```json
"r_20260426181022_a1b2c3d4"
6
{ "engine": "rbf", "fallback_from": [], "vertices": 4225, "faces": 8192 }
{ "engine": "rbf", "fallback_from": [], "resolution_m": 50, "stats": { ... } }
```

Open the generated `.glb` directly in a browser (or in
[gltf.report](https://gltf.report/)) using the `mesh_url` field.

### Real PDF mode (live LLM)

Upload a PDF, extract its text, then run the full pipeline with the
LLM enabled:

```bash
# 1. Upload (returns a content-hash document_id)
DOC_ID=$(curl -s -X POST http://localhost:8000/api/documents/upload \
  -F "file=@./planning/sample-usgs-i2306.pdf" | jq -r .id)

# 2. Run text extraction (PyMuPDF)
curl -s -X POST http://localhost:8000/api/documents/$DOC_ID/extract | jq '.pages_with_text'

# 3. Trigger a full run with the LLM enabled
curl -s -X POST http://localhost:8000/api/run \
  -H "content-type: application/json" \
  -d "{\"document_id\":\"$DOC_ID\",\"use_llm\":true}" | jq '.run_id, .llm_summary, .dsl_summary'
```

If the LLM produces invalid JSON or DSL, the service automatically
runs the **repair loop** up to `GEO_NYC_LLM_MAX_REPAIR_ATTEMPTS` times
before falling back to the fixture path. Either way the response
shape is identical.

---

## Deploying with the Vercel frontend

The frontend is a separate Next.js app deployed at
[geo-nyc.vercel.app](https://geo-nyc.vercel.app). Because the backend
runs on your laptop (not in the cloud), you have to make two things
true:

1. **Vercel can reach the laptop API** → expose port 8000 via a
   tunnel.
2. **Run-manifest URLs are publicly fetchable** → tell the backend
   what its public URL is.

### 1. Tunnel the laptop API

Pick one. You only need one running.

| Tool | Command | Notes |
|---|---|---|
| **ngrok** | `ngrok http 8000` | `https://<random>.ngrok-free.app`. URL rotates on restart on the free plan. |
| **Cloudflare Tunnel** | `brew install cloudflared && cloudflared tunnel --url http://localhost:8000` | Free, more stable URLs with a named tunnel. |
| **Tailscale Funnel** | `tailscale funnel 8000` | Stable `*.ts.net` URL if you already use Tailscale. |

Note the resulting `https://...` URL.

### 2. Backend — bind to all interfaces and advertise the public URL

In `geo-nyc/.env`:

```bash
GEO_NYC_API_HOST=0.0.0.0
GEO_NYC_API_PORT=8000
GEO_NYC_PUBLIC_BASE_URL=https://<your-tunnel-host>
```

`GEO_NYC_PUBLIC_BASE_URL` is what the run service stamps into every
`mesh_url` / `field_url` it writes into a manifest, so the frontend
can fetch them as absolute URLs from any origin.

Restart the backend after editing `.env`.

### 3. CORS — already wired for Vercel

`Settings.cors_origins` ships with `https://geo-nyc.vercel.app`
included by default, and `Settings.cors_origin_regex` accepts every
Vercel preview URL of the form `geo-nyc-*.vercel.app`, so feature
branch deployments work without redeploying the backend.

To allow extra origins (e.g. another teammate's preview):

```bash
GEO_NYC_CORS_ORIGINS=http://localhost:3000,http://localhost:5173,https://geo-nyc.vercel.app,https://other.example.com
GEO_NYC_CORS_ORIGIN_REGEX=^https://geo-nyc(-[a-z0-9-]+)?\.vercel\.app$
```

### 4. Frontend (Vercel) — point at the tunnel

In the Vercel dashboard for `geo-nyc` → Settings → Environment
Variables, set:

```
NEXT_PUBLIC_API_BASE_URL = https://<your-tunnel-host>
```

then trigger a redeploy.

### 5. End-to-end check

From any machine *not* on your laptop's wifi:

```bash
curl https://<your-tunnel-host>/api/health
curl -X POST https://<your-tunnel-host>/api/run -H "content-type: application/json" -d '{}'
```

The second call returns a manifest whose `mesh_url` and `field_url`
are absolute and openable in a browser. If both work, the live demo
pipe is clean.

---

## API contracts

All endpoints are under `/api/*`. Static artifacts live under
`/static/exports/*` and `/static/fields/*`.

### Health

```http
GET /api/health
```

```json
{ "status": "ok", "version": "0.1.0", "use_fixtures": true, "enable_gempy": false }
```

```http
GET /api/llm/health
```

```json
{
  "status": "ok",
  "provider": "ollama",
  "base_url": "http://localhost:11434",
  "model": "llama3.1:8b",
  "model_pulled": true,
  "available_models": ["llama3.1:8b"],
  "detail": null
}
```

### Documents

| Method & path | Purpose | Notes |
|---|---|---|
| `POST /api/documents/upload` (multipart `file`) | Upload a PDF; returns `DocumentRecord` | Idempotent on SHA256 of the bytes. |
| `POST /api/documents/{id}/extract` | Run PyMuPDF text extraction; returns `ExtractionResult` | Cached per document id. |
| `GET /api/documents` | List `DocumentSummary` records | `?limit=` (default 100). |
| `GET /api/documents/{id}` | Full `DocumentRecord` | 404 if unknown. |
| `GET /api/documents/{id}/extraction` | Cached `ExtractionResult` | 404 if not extracted yet. |

### Runs

```http
POST /api/run
Content-Type: application/json

{
  "document_id": null,         // null → fixture mode
  "use_fixtures": null,        // override env default for this run
  "fixture_name": null,        // default: "nyc_demo"
  "use_llm": false,            // requires document_id when true
  "top_k_chunks": null         // 1..32, optional
}
```

Response: a full `RunManifest` (also written to
`data/runs/{run_id}/manifest.json`):

```json
{
  "run_id": "r_20260426181022_a1b2c3d4",
  "status": "succeeded",
  "created_at": "2026-04-26T18:10:22Z",
  "updated_at": "2026-04-26T18:10:23Z",
  "mode": "fixture",
  "request": { "use_llm": false },
  "artifacts": [
    {
      "kind": "mesh",
      "filename": "mesh.glb",
      "relative_path": "r_20260426181022_a1b2c3d4/mesh.glb",
      "url": "http://localhost:8000/static/exports/r_20260426181022_a1b2c3d4/mesh.glb",
      "bytes": 87340,
      "media_type": "model/gltf-binary",
      "metadata": { "engine": "rbf", "vertices": 4225, "faces": 8192 }
    },
    {
      "kind": "field",
      "filename": "depth_to_bedrock.npz",
      "relative_path": "r_20260426181022_a1b2c3d4/depth_to_bedrock.npz",
      "url": "http://localhost:8000/static/fields/r_20260426181022_a1b2c3d4/depth_to_bedrock.npz",
      "bytes": 16512,
      "metadata": { "engine": "rbf", "schema_version": 2 }
    }
    // ...field_meta, dsl, extraction, validation_report
  ],
  "validation": { "is_valid": true, "error_count": 0, "warning_count": 0, "errors": [], "warnings": [] },
  "extent": { "x_min": 0, "x_max": 1000, "y_min": 0, "y_max": 1000, "z_min": -200, "z_max": 0 },
  "mesh_summary": { "engine": "rbf", "fallback_from": [], "duration_ms": 142 },
  "field_summary": { "engine": "rbf", "fallback_from": [], "resolution_m": 50 }
}
```

| Method & path | Purpose |
|---|---|
| `GET /api/run/{run_id}` | Re-fetch a manifest by id. |
| `GET /api/runs?limit=50` | List recent manifests, newest first. |

Status codes:

- `201 Created` for a successful new run.
- `404 Not Found` if `document_id` or `run_id` is unknown.
- `422 Unprocessable Entity` if the run pipeline fails validation
  (e.g. invalid DSL even after the repair loop).

---

## Static asset URLs

The FastAPI app mounts two static dirs:

| Mount | Local dir | Used for |
|---|---|---|
| `/static/exports` | `data/exports/` | Per-run `.glb` mesh files (`<run_id>/mesh.glb`). |
| `/static/fields`  | `data/fields/`  | Per-run `depth_to_bedrock.npz` + `.json` sidecar (`<run_id>/depth_to_bedrock.npz`). |

Each `Artifact.url` in a manifest is built from
`GEO_NYC_PUBLIC_BASE_URL`, so:

- Local dev → `http://localhost:8000/static/exports/r_<timestamp>_<hex8>/mesh.glb`
- Vercel demo → `https://<your-tunnel>/static/exports/r_<timestamp>_<hex8>/mesh.glb`

Run ids are sortable by creation time (`r_YYYYMMDDhhmmss_<hex8>`), so
sorting filenames alphabetically gives newest-last.

The frontend should always use `Artifact.url` directly rather than
joining paths itself.

---

## Field grid schema

Per the team-wide contract in §10.4 of the blueprint, every run
writes a `depth_to_bedrock.npz` plus a JSON sidecar.

**`depth_to_bedrock.npz`** (NumPy v1 archive):

| Key | Shape | dtype | Meaning |
|---|---|---|---|
| `grid` | `(ny, nx)` | `float32` | Depth from ground to bedrock, **meters below surface**. |
| `x` | `(nx,)` | `float32` | Easting coordinates of grid columns (projected CRS). |
| `y` | `(ny,)` | `float32` | Northing coordinates of grid rows (projected CRS). |
| `mask` | `(ny, nx)` | `uint8` | *Optional* — `1` for valid cells, `0` for outside-AOI / nodata. |

**`depth_to_bedrock.json`** (sidecar):

```json
{
  "schema_version": 2,
  "name": "depth_to_bedrock_m",
  "units": "meters_below_surface",
  "source": "rbf",
  "run_id": "r_20260426181022_a1b2c3d4",
  "crs": "EPSG:32618",
  "projected_crs": "EPSG:32618",
  "geographic_crs": "EPSG:4326",
  "bbox": [-74.05, 40.66, -73.92, 40.81],
  "bbox_xy_m": { "x_min": 583000.0, "x_max": 595000.0, "y_min": 4505000.0, "y_max": 4521000.0 },
  "extent": { "x_min": 583000.0, "x_max": 595000.0, "y_min": 4505000.0, "y_max": 4521000.0, "z_min": -250.0, "z_max": 50.0 },
  "resolution_m": 50,
  "nx": 64, "ny": 64,
  "shape": [64, 64],
  "dtype": "float32",
  "has_mask": false,
  "stats": { "min": 0.5, "max": 187.3, "mean": 42.8, "valid_cells": 4096 }
}
```

`source` is always one of `"gempy" | "rbf" | "synthetic" | "stub"`,
recording the actual fallback that produced the field. The 3-tier
chain in priority order is:

1. **RBF** interpolation from `GemPyInputs.surface_points` (preferred —
   the LLM's evidence drives the surface).
2. **Mesh resample** from the bedrock layer of the produced `.glb`.
3. **Stub** — a deterministic smooth field. Always succeeds.

---

## Environment variables

All variables are prefixed `GEO_NYC_` and loaded from `.env` by
`pydantic-settings`.

| Variable | Default | Purpose |
|---|---|---|
| `GEO_NYC_LLM_PROVIDER` | `ollama` | Only `ollama` is supported. Cloud providers are intentionally absent. |
| `GEO_NYC_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama HTTP base URL. |
| `GEO_NYC_OLLAMA_MODEL` | `llama3.1:8b` | Model used for extraction. Pull with `ollama pull`. |
| `GEO_NYC_OLLAMA_FAST_MODEL` | *unset* | Optional override for relevance / repair calls. |
| `GEO_NYC_LLM_TEMPERATURE` | `0.2` | Sampling temperature. |
| `GEO_NYC_LLM_MAX_TOKENS` | `4096` | Max tokens per LLM call. |
| `GEO_NYC_LLM_TIMEOUT_SECONDS` | `120` | Per-call timeout. |
| `GEO_NYC_LLM_MAX_REPAIR_ATTEMPTS` | `2` | Repair loop iterations on invalid JSON / DSL. |
| `GEO_NYC_API_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` when tunneling. |
| `GEO_NYC_API_PORT` | `8000` | API port. |
| `GEO_NYC_DEBUG` | `false` | Reload + verbose logs. |
| `GEO_NYC_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173,https://geo-nyc.vercel.app` | Comma-separated allowed origins. |
| `GEO_NYC_CORS_ORIGIN_REGEX` | `^https://geo-nyc(-[a-z0-9-]+)?\.vercel\.app$` | Regex matched against `Origin` for Vercel previews. |
| `GEO_NYC_PUBLIC_BASE_URL` | `http://localhost:8000` | Used to build absolute artifact URLs in run manifests. |
| `GEO_NYC_DATA_DIR` | `./data` | Root for all on-disk artifacts. |
| `GEO_NYC_DOCUMENTS_RAW_DIR` | `./data/documents/raw` | Uploaded PDFs. |
| `GEO_NYC_DOCUMENTS_EXTRACTED_DIR` | `./data/documents/extracted` | Cached PyMuPDF extractions. |
| `GEO_NYC_RUNS_DIR` | `./data/runs` | Per-run manifests + intermediate artifacts. |
| `GEO_NYC_EXPORTS_DIR` | `./data/exports` | Per-run `.glb` mesh files (`/static/exports/<run_id>/`). |
| `GEO_NYC_FIELDS_DIR` | `./data/fields` | Per-run `depth_to_bedrock.npz` + sidecar (`/static/fields/<run_id>/`). |
| `GEO_NYC_CACHE_DIR` | `./data/cache` | Misc cache (relevance scores, etc.). |
| `GEO_NYC_USE_FIXTURES` | `true` | When true, `/api/run` defaults to fixture-mode artifacts. |
| `GEO_NYC_ENABLE_GEMPY` | `false` | When true, `GemPyRunner` is preferred over `RBFRunner`. |
| `GEO_NYC_LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

Trailing slashes on `OLLAMA_BASE_URL` and `PUBLIC_BASE_URL` are
stripped automatically.

---

## Manual smoke checklist

Run this before a live demo or after a clean pull. (Phase 12.3.)

- [ ] `ollama serve` is running and `ollama list` shows `llama3.1:8b`.
- [ ] `curl http://localhost:11434/api/tags` returns the model list.
- [ ] `.venv` is active and `python -c "import geo_nyc; print(geo_nyc.__version__)"` works.
- [ ] `pytest -q` passes (currently **192 passed, 1 skipped**).
- [ ] `geo-nyc` (or `uvicorn api.main:app`) starts cleanly.
- [ ] `curl http://localhost:8000/api/health` → `"ok"`.
- [ ] `curl http://localhost:8000/api/llm/health` → `"ok"`.
- [ ] `POST /api/run` with `{}` returns a manifest in <2s.
- [ ] `POST /api/documents/upload` with a real USGS PDF returns a content-hash id.
- [ ] `POST /api/documents/{id}/extract` returns `pages_with_text > 0`.
- [ ] `POST /api/run` with `{ "document_id": "...", "use_llm": true }` returns a manifest with `llm_summary` non-null.
- [ ] `mesh_url` from the manifest opens in a browser / `gltf.report`.
- [ ] When tunneling: same `mesh_url` opens from a phone on cellular.

---

## Tests, lint, and CI

```bash
# Full suite (unit + integration)
pytest -q

# Lint
ruff check geo_nyc api tests

# Type check (mypy is configured in pyproject.toml)
mypy geo_nyc api
```

Test guidelines:

- LLM tests use `app.dependency_overrides[get_run_service]` to inject
  stubbed services, so they never hit the network.
- `tests/test_gempy_runner.py` is auto-skipped when `gempy` is not
  installed.
- Integration tests for the field fallback chain live in
  `tests/test_run_field_fallback.py`.

---

## Repository layout

```
geo-nyc/
├── api/                          # FastAPI app + routers
│   ├── main.py                   # create_app(), CORS, static mounts, lifespan
│   ├── schemas.py                # Public API Pydantic models
│   └── routers/                  # /health, /documents, /run(s)
│
├── geo_nyc/                      # Core package
│   ├── ai/                       # Ollama HTTP client + repair-aware extractor
│   ├── config.py                 # Pydantic settings (env-driven)
│   ├── documents/                # PDF upload, PyMuPDF extraction, content-hash IDs
│   ├── extraction/               # Chunking + relevance ranking
│   ├── prompts/                  # nyc_geology_extraction.md, repair_extraction.md
│   ├── parsers/                  # Lark DSL grammar, parser, validator, builder
│   ├── modeling/                 # Constraints, RBF/GemPy/Synthetic runners,
│   │                             # mesh export, field builder + exporter
│   └── runs/                     # RunService, RunManifest, fixtures
│
├── tests/                        # pytest suite (192+ tests)
├── data/                         # On-disk artifacts (gitignored at runtime)
│   ├── fixtures/                 # Bundled demo fixtures
│   ├── documents/                # raw PDFs + extracted JSON
│   ├── runs/                     # per-run manifests + artifacts
│   ├── exports/                  # /static/exports/<run_id>/mesh.glb
│   └── fields/                   # /static/fields/<run_id>/depth_to_bedrock.{npz,json}
│
├── geo-lm/                       # (sibling) Reference repo, NOT imported
├── planning/                     # Master blueprint + phase tasks
│
├── pyproject.toml                # Python 3.12, deps, ruff, pytest config
├── .env.example                  # Copy to .env and edit
└── README.md                     # ← you are here
```

---

## Troubleshooting

### Ollama not reachable

```bash
curl http://localhost:11434/api/tags
```

Should return JSON. If it fails:

- Run `ollama serve` (in a separate terminal).
- Confirm no firewall/VPN is blocking `localhost:11434`.
- If `/api/llm/health` says `"down"`, the FastAPI app's
  `GEO_NYC_OLLAMA_BASE_URL` may be wrong — restart after editing
  `.env`.

### Model missing

```bash
ollama list
ollama pull llama3.1:8b
```

Make sure `GEO_NYC_OLLAMA_MODEL` matches the exact tag printed by
`ollama list` (including the `:8b` suffix).

### Python import failures

- Confirm the venv is active (`which python` should be inside `.venv/bin`).
- Check Python version: `python --version` must be 3.12.x.
- Reinstall: `pip install -e ".[dev]"`.
- Don't mix Poetry and `.venv` — pick one toolchain.

### LLM output invalid (JSON or DSL)

The repair loop handles most of these automatically. If it still
fails:

- Lower `GEO_NYC_LLM_TEMPERATURE` (try `0.0`).
- Reduce `GEO_NYC_LLM_MAX_TOKENS` if the model is rambling.
- Check `data/runs/{run_id}/llm_attempts/` — every prompt and raw
  response is persisted for offline debugging.
- As a last resort, re-run with `use_llm=false` to fall back to the
  fixture path.

### GemPy fails (or is missing)

- Without `gempy` installed, `RBFRunner` is the default and always
  works — `mesh_summary.engine` will be `"rbf"`.
- If GemPy *is* installed but raises during import on Apple Silicon,
  set `GEO_NYC_ENABLE_GEMPY=false` and rely on RBF for the demo. The
  manifest records the fallback chain so judges still see the
  intent.

### Frontend cannot load mesh / CORS error

- Open `mesh_url` directly in a browser: it should download the
  `.glb` without auth.
- Confirm `GEO_NYC_PUBLIC_BASE_URL` matches the public URL (it goes
  into every manifest, so old runs may have stale URLs — re-run
  after changing it).
- Check the browser DevTools Console for the exact origin: it must
  be present in `GEO_NYC_CORS_ORIGINS` or matched by
  `GEO_NYC_CORS_ORIGIN_REGEX`.
- For Vercel previews, `geo-nyc-*.vercel.app` should be matched by
  the default regex; if not, run
  `python -c "import re; print(re.fullmatch(r'^https://geo-nyc(-[a-z0-9-]+)?\.vercel\.app$', 'https://YOUR-PREVIEW-URL'))"`
  and adjust the regex.

### Field grid looks weird / discontinuous

The field can fall back through three tiers (RBF → mesh resample →
stub). Check `field_summary.engine` and `field_summary.fallback_from`
in the manifest. If you're hitting `"stub"`, the LLM didn't produce
enough surface-point evidence — increase `top_k_chunks` or feed a
denser PDF.

### `ngrok` URL changes every restart

Free-tier ngrok issues a new hostname on each session, which means
both `GEO_NYC_PUBLIC_BASE_URL` and the Vercel `NEXT_PUBLIC_API_BASE_URL`
need to be updated. For a stable URL, use `cloudflared` named
tunnels or a paid ngrok reserved domain.

---

## Project blueprint

The sections below are the original team-wide blueprint (frontend
strategy, GIS workstream, optimization, demo run-of-show). They are
preserved here for new contributors.

### 1. The Core Repository & Frontend Strategy

#### 1.1 Starting point: `geo-lm`
We use **[williamjsdavis/geo-lm](https://github.com/williamjsdavis/geo-lm)** as a
**reference** repository (MIT license). It demonstrates the hard parts
we needed: **PDF upload → document processing → geology DSL → GemPy-
oriented 3D workflow**, with a **FastAPI** backend (`api/`), core
package (`geo_lm/`), and an upstream **React + Vite** demo UI
(`web/`) — **reference only**; our shipped UI is **Next.js on Vercel**.

**What we keep from upstream:**
* **REST shape:** document upload/extract, DSL parse/validate/create, workflow status endpoints.
* **DSL pipeline:** structured "geology DSL" as the contract between language understanding and modeling (Lark grammar, validation, retries).
* **GemPy integration path:** implicit 3D geological modeling after DSL is stable.

**What we re-implemented for this hackathon:**
* **Local inference only:** upstream defaults to cloud providers (Anthropic, OpenAI, Llama API keys in `.env`). We replaced the LLM client layer with an **Ollama** adapter (`geo_nyc.ai`).
* **NYC corpus:** generic upstream examples were swapped for the **USGS NYC PDFs** below; prompts encode **NYC stratigraphy** (formations, contacts, depths).
* **Frontend strategy:** FastAPI backend stays in `geo-nyc`. The product UI is a separate Next.js app on Vercel — **not** a fork of upstream `web/`.

### 2. UI / UX Design Requirements
* **Aesthetic:** minimalistic, white background, no dark/cyberpunk themes.
* **Geospatial Mapping Layers:** judges can toggle:
    1. Base 2D street map.
    2. NYC borough outline (Manhattan, Bronx, Queens — see §6).
    3. NYC Open Data layers (flood, infrastructure context).
    4. Optional ML-derived rasters (smoothed depth-to-bedrock).
    5. 3D subsurface mesh (`.glb` from this backend).
* **Optimization UI:** a small "What-if" panel driving §8's toy
  optimizer against precomputed scalar fields — never blocking the
  UI on a full GemPy solve.

### 3. Data Acquisition: The Target PDFs
1. **Master Stratigraphy:** *Bedrock and Engineering Geologic Maps of Bronx County and parts of New York and Queens Counties (USGS I-2306)*.
2. **Infrastructure Risk Data:** *Newly Mapped Walloomsac Formation in Lower Manhattan and New York Harbor and the Implications for Engineers*.
3. **Depth Metrics:** *Stratigraphy, Structural Geology and Metamorphism of the Inwood Marble Formation, Northern Manhattan (NYC Water Tunnel Data)*.

### 4. End-to-End Pipeline (mapped to this repo)

| Phase | Repo path | Status |
|---|---|---|
| **A. Local AI engine** | `geo_nyc/ai/` (Ollama HTTPX client) | done |
| **B. Document ingestion** | `geo_nyc/documents/`, `/api/documents/*` | done |
| **B'. Chunking + relevance ranking** | `geo_nyc/extraction/` | done |
| **C. LLM → DSL** | `geo_nyc/prompts/`, `geo_nyc/parsers/dsl/` | done |
| **D. 3D modeling bridge** | `geo_nyc/modeling/{rbf,gempy,synthetic}_runner.py` | done (RBF default, GemPy optional) |
| **D'. Scalar field export** | `geo_nyc/modeling/field_{builder,export}.py` | done |
| **E. Optimization** | *Owned by Part 3* — `/api/optimize` | out-of-scope for this repo |
| **F. Deployment** | `cors_origins`, `cors_origin_regex`, `public_base_url` | done |

### 5. The Pitch
*"City planners waste months manually extracting data from 400-page
USGS PDFs to plan geothermal grids and subway expansions. We built
Urban Subsurface AI. It uses a 100% local, edge-computed AI agent to
read those unstructured reports and instantly generate interactive
3D infrastructure models. No cloud latency, absolute data privacy,
and a seamless visual pipeline to build the city of the future
safely."*

### 6. NYC Open Data (Part 3)

Owned by Part 3 (separate workstream), not this repo. Key contracts:

- **AOI:** Manhattan + Bronx + Queens (BoroCode 1, 2, 4) clipped from
  [Borough Boundaries](https://data.cityofnewyork.us/City-Government/Borough-Boundaries/gthc-hcne).
- **Outputs:** `data/layers/*.geojson` + `data/layers/manifest.json`,
  synced to the Next.js repo's `public/layers/`.
- **CRS:** all browser GeoJSON in **EPSG:4326**; server-side analytic
  work in a projected CRS (suggested **EPSG:32618 / UTM 18N**).

### 7. ML Field Smoothing (overlap with Part 3)

This repo's contribution: per-run `depth_to_bedrock.npz` produced by
RBF interpolation from LLM-derived surface points. Schema in
[Field grid schema](#field-grid-schema).

Part 3 may consume this and produce GeoJSON contours in
`data/layers/depth_contours.geojson`.

### 8. Optimization API (Part 3)

`POST /api/optimize` is owned by Part 3 and is **not** implemented in
this backend. It reads `depth_to_bedrock.npz` and returns:

```json
{ "optimal_d": 24.3, "objective": 1.27, "constraints_ok": true, "diagnostics": { ... } }
```

### 9. Team Workstreams

- **Part 1 (Frontend):** Next.js on Vercel — MapLibre + R3F.
- **Part 2 (this repo):** FastAPI, Ollama, RBF/GemPy, mesh + field
  exports.
- **Part 3 (GIS / Optimizer):** Open Data ingest, Kriging, `/api/optimize`.

### 10. Integration contracts

The full contract list (file layouts, API shapes, CRS, demo
run-of-show) lives in `planning/part-2-design.md` and
`planning/part-2-tasks.md`. The runtime API contract for Part 1 + 3
is the [API contracts](#api-contracts) section above — that is
authoritative.

---

## License

- **geo-nyc** code: MIT.
- **geo-lm** reference code: MIT — preserved attribution where re-used in spirit.
- **NYC Open Data:** per-dataset terms; provenance recorded in `manifest.json`.
- **USGS reports:** public domain; cited in the app About panel.
