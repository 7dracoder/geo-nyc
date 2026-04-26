# Part 2 Requirements: Local LLM, Extraction, GemPy, and Artifact Export

## Purpose
This document defines everything Member B needs to set up and build the Part 2 backend: local Ollama inference, Python virtual environment, document extraction, DSL validation, GemPy modeling, mesh export, scalar field export, API contracts, tests, and demo reliability.

## Success Criteria
Part 2 is complete when:

1. The backend runs locally from a Python virtual environment.
2. Ollama is installed, running, and reachable from Python.
3. At least one NYC USGS PDF can be processed through the pipeline.
4. The pipeline creates a validated structured geology output.
5. The pipeline writes a `.gltf` or `.glb` mesh artifact.
6. The pipeline writes `data/fields/depth.npz` and `data/fields/depth_meta.json`.
7. `POST /api/run` returns stable artifact paths for frontend and Part 3.
8. The entire demo can run without cloud LLM API keys.
9. A fixture fallback exists and can produce artifacts even if the live LLM extraction fails.

## Local Machine Requirements
Target machine:

1. macOS on Apple Silicon.
2. M4 Pro with 24 GB unified memory.
3. At least 20 GB free disk space for models, PDFs, venv, and artifacts.
4. Stable internet only for setup/downloads. Demo path should run locally afterward.

Recommended tools:

1. Homebrew.
2. Python 3.11 or 3.12.
3. Git.
4. Ollama.
5. VS Code/Cursor.
6. Optional: Cloudflare Tunnel or Ngrok for frontend integration day.

## Manual Setup: System Tools

### 1. Install Homebrew if missing
Check:

```bash
brew --version
```

If not installed, install from the official Homebrew instructions, then restart the terminal.

### 2. Install Python
Use Python 3.11 or 3.12. Prefer Homebrew if system Python is old:

```bash
brew install python@3.12
python3.12 --version
```

If the `geo-lm` fork requires Python 3.12+, use Python 3.12.

### 3. Install Git
Check:

```bash
git --version
```

If needed:

```bash
brew install git
```

## Manual Setup: Ollama

### 1. Install Ollama

```bash
brew install ollama
```

Or use the official macOS installer if preferred.

### 2. Start Ollama
Usually the app keeps the server running. To test:

```bash
ollama --version
curl http://localhost:11434/api/tags
```

If the server is not running:

```bash
ollama serve
```

Keep that terminal open, or launch Ollama as a background app.

### 3. Pull the default model
Use an 8B-class model first:

```bash
ollama pull llama3.1:8b
```

If unavailable, use:

```bash
ollama pull llama3:8b
```

### 4. Smoke test the model

```bash
ollama run llama3.1:8b "Return only JSON: {\"ok\": true}"
```

Expected: a short JSON-like answer. If it adds text, that is normal early on; the implementation will enforce validation and repair.

### 5. Ollama performance notes
On M4 Pro / 24 GB:

1. Do not run multiple large local models at once.
2. Close memory-heavy apps before full runs.
3. Use chunking; do not send full PDFs to the model.
4. Keep default temperature low.
5. Prefer one 8B model for demo reliability.

## Manual Setup: Backend Repo

### 1. Clone or enter the backend repo
The current planning repo is:

```bash
cd /Users/somaditya/Desktop/geoNYC/geo-nyc
```

If this repo has not yet been replaced with the actual `geo-lm` fork, do that before implementation:

```bash
git clone https://github.com/williamjsdavis/geo-lm.git geo-nyc-backend
```

Then apply the Part 2 changes there. The plan assumes the backend repo eventually contains:

```text
api/
geo_lm/
tests/
pyproject.toml
```

### 2. Create a Python virtual environment
Always use a local venv for Part 2:

```bash
cd /path/to/backend-repo
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

Verify:

```bash
which python
python --version
pip --version
```

Expected: paths point inside `.venv`.

### 3. Install upstream dependencies
If the repo uses Poetry:

```bash
pip install poetry
poetry install
```

If you want Poetry to use the active venv:

```bash
poetry config virtualenvs.create false --local
poetry install
```

If using pip instead of Poetry, install editable mode:

```bash
pip install -e .
```

### 4. Install Part 2 required packages
Exact versions can be finalized after seeing the forked `pyproject.toml`. Minimum package families:

```bash
pip install fastapi uvicorn httpx pydantic python-dotenv pymupdf numpy scipy pandas
pip install gempy trimesh pygltflib
pip install pytest pytest-asyncio respx
```

Notes:

1. `httpx` handles Ollama HTTP calls.
2. `pymupdf` handles PDF text extraction.
3. `gempy` handles geological modeling.
4. `trimesh` / `pygltflib` help export `.gltf` if GemPy does not directly produce the desired asset.
5. Part 3 may add geospatial packages separately; Part 2 only needs to produce fields/artifacts.

### 5. Save dependencies
If using Poetry:

```bash
poetry add httpx python-dotenv pymupdf numpy scipy pandas gempy trimesh pygltflib
poetry add --group dev pytest pytest-asyncio respx
```

If using pip:

```bash
pip freeze > requirements.txt
```

For the hackathon, prefer Poetry if upstream already uses it. Avoid fighting the existing packaging system.

## Environment Variables
Create `.env` in the backend repo. Do not commit it.

```text
GEO_NYC_ENV=local
GEO_NYC_LLM_PROVIDER=ollama
GEO_NYC_OLLAMA_BASE_URL=http://localhost:11434
GEO_NYC_OLLAMA_MODEL=llama3.1:8b
GEO_NYC_OLLAMA_TEMPERATURE=0.1
GEO_NYC_OLLAMA_NUM_CTX=8192
GEO_NYC_LLM_TIMEOUT_SECONDS=180
GEO_NYC_MAX_DSL_RETRIES=4
GEO_NYC_ALLOW_FIXTURE_FALLBACK=true
GEO_NYC_DATA_DIR=./data
GEO_NYC_STATIC_EXPORTS_URL_PREFIX=/static/exports
GEO_NYC_CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

If deployed behind a tunnel, add the Vercel URL and tunnel URL to CORS:

```text
GEO_NYC_CORS_ORIGINS=http://localhost:3000,https://your-app.vercel.app
```

## Data Directory Requirements
Create:

```bash
mkdir -p data/documents/raw
mkdir -p data/documents/extracted
mkdir -p data/runs
mkdir -p data/exports
mkdir -p data/fields
mkdir -p data/fixtures
```

Recommended `.gitignore` entries:

```text
.venv/
.env
data/documents/raw/
data/runs/
data/exports/
data/fields/
*.log
```

For the hackathon, keep tiny fixture files committed only if they are small and legal to share.

## USGS PDF Requirements
Target PDFs:

1. *Bedrock and Engineering Geologic Maps of Bronx County and parts of New York and Queens Counties (USGS I-2306)*
2. *Newly Mapped Walloomsac Formation in Lower Manhattan and New York Harbor and the Implications for Engineers*
3. *Stratigraphy, Structural Geology and Metamorphism of the Inwood Marble Formation, Northern Manhattan (NYC Water Tunnel Data)*

Manual steps:

1. Download PDFs from official USGS or publication pages.
2. Save under `data/documents/raw/`.
3. Use readable names:

```text
data/documents/raw/usgs-i-2306-bronx-ny-queens.pdf
data/documents/raw/walloomsac-lower-manhattan-harbor.pdf
data/documents/raw/inwood-marble-water-tunnel.pdf
```

Do not assume every PDF has perfect embedded text. First implementation uses PyMuPDF text extraction; OCR is a future extension.

## Functional Requirements

### FR1: Ollama Health Check
The backend must expose or implement an internal health check that confirms:

1. Ollama base URL reachable.
2. Model exists.
3. A short structured prompt returns a response.

Recommended endpoint:

```http
GET /api/llm/health
```

Response:

```json
{
  "provider": "ollama",
  "base_url": "http://localhost:11434",
  "model": "llama3.1:8b",
  "ok": true
}
```

### FR2: PDF Upload and Extraction
The backend must accept PDFs and extract text with page references.

Required output:

```json
{
  "document_id": "doc123",
  "pages": [
    {
      "page": 1,
      "text": "..."
    }
  ]
}
```

### FR3: Chunk Ranking
The backend must split extraction output into chunks and rank them for geology relevance.

Required:

1. Deterministic chunk IDs.
2. Page references.
3. Score.
4. Keywords matched.

### FR4: LLM Extraction
The backend must call Ollama with selected chunks and request strict structured geology output.

Required:

1. Timeout handling.
2. Retry handling.
3. Raw LLM output saved per run.
4. Prompt version saved per run.

### FR5: Validation
The backend must validate model output before modeling.

Required:

1. Pydantic schema validation.
2. Unit normalization to meters.
3. Formation name normalization.
4. Evidence quote required for extracted claims.
5. Repair loop for invalid output.

### FR6: DSL Generation
The backend must produce a geology DSL file or maintain compatibility with upstream DSL parser.

Required artifact:

```text
data/runs/{run_id}/geology.dsl
```

### FR7: GemPy Input Generation
The backend must convert validated constraints into GemPy input objects or an intermediate JSON.

Required artifact:

```text
data/runs/{run_id}/gempy_inputs.json
```

### FR8: Mesh Export
The backend must export a frontend-loadable 3D asset.

Required:

1. `.gltf` preferred, `.glb` acceptable.
2. Stored under `data/exports/`.
3. Static URL returned by `/api/run`.
4. Fallback sample asset available.

### FR9: Field Export
The backend must export a scalar field for Part 3.

Required:

1. `data/fields/depth.npz`
2. `data/fields/depth_meta.json`
3. Schema matches the integration contract.
4. Stub mode supported.

### FR10: Run API
The backend must provide one orchestration endpoint.

```http
POST /api/run
GET /api/run/{run_id}
```

### FR11: Static Export Serving
The backend must serve exported mesh files:

```text
/static/exports/{run_id}.gltf
```

### FR12: Fixture Mode
The backend must support:

1. Fixture extraction JSON.
2. Fixture DSL.
3. Fixture GemPy inputs.
4. Fixture field.

This is required for demo stability.

## Non-Functional Requirements

### NFR1: Local-Only Inference
No cloud LLM requests in the default path.

### NFR2: Reproducibility
Every run writes:

```text
run_manifest.json
prompt_version.txt
raw_llm_output.txt
validation_report.json
```

### NFR3: Observability
Logs must include:

1. Run ID.
2. Stage name.
3. Duration.
4. Warning/error details.
5. Whether fixture fallback was used.

### NFR4: Performance
Targets:

1. LLM request per chunk under 180 seconds.
2. Cached fixture run under 15 seconds.
3. Full single-PDF run under 5 minutes.
4. Exported mesh under 25 MB.

### NFR5: Resilience
Failures should be explicit JSON responses, not crashes.

### NFR6: Privacy
Raw PDFs and extracted text stay local.

### NFR7: Compatibility
Outputs must be consumable by:

1. Next.js frontend over HTTP or copied static files.
2. Part 3 scripts through file paths.

## API Requirements

### `GET /api/health`
Response:

```json
{
  "ok": true,
  "service": "geo-nyc-backend"
}
```

### `GET /api/llm/health`
Response:

```json
{
  "ok": true,
  "provider": "ollama",
  "model": "llama3.1:8b"
}
```

### `POST /api/run`
Request:

```json
{
  "document_ids": ["doc123"],
  "use_cached": true,
  "allow_fixture_fallback": true
}
```

Response:

```json
{
  "run_id": "demo-001",
  "status": "ok",
  "gltf_path": "/static/exports/demo-001.gltf",
  "depth_field_path": "data/fields/depth.npz",
  "depth_meta_path": "data/fields/depth_meta.json",
  "warnings": []
}
```

### `GET /api/run/{run_id}`
Response:

```json
{
  "run_id": "demo-001",
  "status": "ok",
  "stage": "complete",
  "artifacts": {
    "gltf": "/static/exports/demo-001.gltf",
    "depth_field": "data/fields/depth.npz"
  },
  "warnings": []
}
```

## Artifact Requirements

### `run_manifest.json`

```json
{
  "run_id": "demo-001",
  "created_at": "2026-04-26T00:00:00Z",
  "documents": [],
  "model": "llama3.1:8b",
  "provider": "ollama",
  "used_fixture_fallback": false,
  "artifacts": {
    "dsl": "data/runs/demo-001/geology.dsl",
    "gltf": "data/exports/demo-001.gltf",
    "depth_field": "data/fields/depth.npz",
    "depth_meta": "data/fields/depth_meta.json"
  },
  "warnings": []
}
```

### `validation_report.json`

```json
{
  "valid": true,
  "errors": [],
  "warnings": [],
  "normalized_units": true,
  "formation_count": 3,
  "contact_count": 2
}
```

## Development Quality Gates
Before Part 2 is considered ready:

1. `python -m pytest` passes.
2. `GET /api/llm/health` returns ok while Ollama is running.
3. Fixture run writes all required artifacts.
4. At least one real PDF extraction produces a validation report.
5. Frontend can load the exported mesh path.
6. Part 3 can open `depth.npz` and read `depth_meta.json`.

## Demo-Day Checklist
Before presenting:

1. Start Ollama.
2. Confirm model is pulled.
3. Activate `.venv`.
4. Start FastAPI.
5. Run fixture smoke test.
6. Run cached real demo once.
7. Confirm `.gltf` URL loads in browser.
8. Confirm `depth.npz` exists.
9. Confirm `/api/run/{run_id}` returns ok.
10. Keep a known-good `run_id` written down.

## Known Risks
1. PDFs may not have clean embedded text.
2. Ollama may produce invalid JSON/DSL.
3. GemPy may need more constraints than extracted text provides.
4. Mesh export may require a custom `trimesh` path.
5. Large grids or meshes may be too slow for live demo.

## Required Mitigations
1. Keep fixture DSL.
2. Keep stub field export.
3. Keep sample mesh export.
4. Use chunking and low temperature.
5. Cache successful demo artifacts.
