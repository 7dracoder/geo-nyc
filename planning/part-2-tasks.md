# Part 2 Tasks: Execution Plan for Member B

## How to Use This File
Work top to bottom. Each phase should leave behind a working checkpoint. Do not wait for perfect geology before shipping the scaffolding: the frontend and Part 3 need stable files and endpoints early.

Status convention:

```text
[ ] not started
[/] in progress
[x] done
[!] blocked
```

## Phase 0 — Repo and Setup

### 0.1 Confirm backend repo shape
- [ ] Decide whether this current `geo-nyc` repo will become the backend fork or whether the actual `geo-lm` fork will be cloned separately.
- [ ] Ensure backend repo contains or will contain:
  - `api/`
  - `geo_lm/`
  - `tests/`
  - `pyproject.toml`
  - `README.md`
- [ ] Preserve upstream MIT license attribution from `geo-lm`.

Done when: the backend repo is clearly identified and everyone knows where Part 2 code will land.

### 0.2 Create local virtual environment
Run inside backend repo:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

- [ ] Confirm `which python` points to `.venv`.
- [ ] Confirm `python --version` is 3.11 or 3.12.
- [ ] Add `.venv/` to `.gitignore`.

Done when: shell prompt uses the venv and `pip --version` points inside `.venv`.

### 0.3 Install dependencies
If upstream uses Poetry:

```bash
pip install poetry
poetry config virtualenvs.create false --local
poetry install
```

Then add Part 2 packages:

```bash
poetry add httpx python-dotenv pymupdf numpy scipy pandas gempy trimesh pygltflib
poetry add --group dev pytest pytest-asyncio respx
```

If using pip:

```bash
pip install fastapi uvicorn httpx pydantic python-dotenv pymupdf numpy scipy pandas
pip install gempy trimesh pygltflib
pip install pytest pytest-asyncio respx
pip freeze > requirements.txt
```

- [ ] Pick Poetry or pip based on actual repo.
- [ ] Install dependencies.
- [ ] Run `python -c "import fastapi, httpx, fitz, numpy"` successfully.
- [ ] Run `python -c "import gempy"` successfully, or document GemPy install blocker.

Done when: core imports work in `.venv`.

### 0.4 Install and smoke-test Ollama

```bash
brew install ollama
ollama pull llama3.1:8b
curl http://localhost:11434/api/tags
ollama run llama3.1:8b "Return only JSON: {\"ok\": true}"
```

- [ ] Ollama installed.
- [ ] Model pulled.
- [ ] Local server reachable.
- [ ] Record chosen model in `.env`.

Done when: `curl http://localhost:11434/api/tags` returns models.

### 0.5 Create backend directories

```bash
mkdir -p data/documents/raw
mkdir -p data/documents/extracted
mkdir -p data/runs
mkdir -p data/exports
mkdir -p data/fields
mkdir -p data/fixtures
```

- [ ] Add generated data directories to `.gitignore` as appropriate.
- [ ] Keep only tiny fixtures committed.

Done when: directories exist.

### 0.6 Create `.env`
Create:

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
GEO_NYC_CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

- [ ] Add `.env` to `.gitignore`.
- [ ] Add `.env.example` with safe defaults.

Done when: app can load config from `.env`.

## Phase 1 — Baseline FastAPI and Health

### 1.1 Start upstream API
- [ ] Run existing API:

```bash
uvicorn api.main:app --reload --port 8000
```

or:

```bash
poetry run uvicorn api.main:app --reload --port 8000
```

- [ ] Open `http://localhost:8000/docs`.
- [ ] Confirm existing health endpoint, if any.

Done when: FastAPI docs load.

### 1.2 Add config loader
Create or extend config module:

```text
geo_lm/config.py
```

or:

```text
geo_nyc/config.py
```

Config must expose:

- [ ] Ollama base URL.
- [ ] Ollama model.
- [ ] temperature.
- [ ] num context.
- [ ] timeout.
- [ ] data dir.
- [ ] CORS origins.
- [ ] fixture fallback flag.

Done when: tests can instantiate settings without real network.

### 1.3 Add CORS setup
- [ ] Read `GEO_NYC_CORS_ORIGINS`.
- [ ] Add FastAPI CORS middleware.
- [ ] Include `http://localhost:3000` for Next dev.
- [ ] Later include Vercel origin.

Done when: browser frontend can call backend locally.

### 1.4 Add health endpoints
Add:

```http
GET /api/health
GET /api/llm/health
```

- [ ] `/api/health` does not depend on Ollama.
- [ ] `/api/llm/health` checks Ollama tags or short generate request.
- [ ] Return structured error if Ollama is down.

Done when: both endpoints tested with `curl`.

## Phase 2 — Ollama Client

### 2.1 Create client module
Create:

```text
geo_lm/ai/ollama_client.py
```

Responsibilities:

- [ ] `generate(prompt, system=None, options=None)`.
- [ ] `chat(messages, options=None)` if using chat endpoint.
- [ ] Timeout and retry configuration.
- [ ] Raise typed errors for connection, timeout, and invalid response.
- [ ] Save raw response when called from run pipeline.

Done when: client can call local Ollama from Python.

### 2.2 Provider selection
- [ ] Add provider registry or config branch:

```text
GEO_NYC_LLM_PROVIDER=ollama
```

- [ ] Default to Ollama.
- [ ] Do not require cloud keys.
- [ ] Preserve cloud provider code only if upstream needs it, but keep it out of default path.

Done when: app starts with no Anthropic/OpenAI/Llama API key.

### 2.3 Mock tests
Add tests with mocked HTTP:

- [ ] Successful generate parse.
- [ ] Ollama down error.
- [ ] Timeout error.
- [ ] Invalid JSON response shape.

Done when: tests pass without Ollama running.

## Phase 3 — PDF Extraction

### 3.1 Download reports manually
Save:

```text
data/documents/raw/usgs-i-2306-bronx-ny-queens.pdf
data/documents/raw/walloomsac-lower-manhattan-harbor.pdf
data/documents/raw/inwood-marble-water-tunnel.pdf
```

- [ ] Download from official sources.
- [ ] Confirm files open locally.
- [ ] Do not commit large PDFs unless team decides.

Done when: three PDFs exist locally.

### 3.2 Implement extraction wrapper
Create or extend:

```text
geo_lm/documents/pdf_extractor.py
```

Output:

```json
{
  "document_id": "...",
  "pages": [
    {"page": 1, "text": "..."}
  ]
}
```

- [ ] Use PyMuPDF (`fitz`).
- [ ] Preserve page number.
- [ ] Handle empty pages.
- [ ] Write extracted text to `data/documents/extracted/{document_id}.json`.

Done when: one PDF extracts text to JSON.

### 3.3 API integration
- [ ] Reuse upstream `/api/documents/upload` if present.
- [ ] Reuse or create `/api/documents/{id}/extract`.
- [ ] Ensure local file paths are not leaked unexpectedly.

Done when: upload/extract works from Swagger docs or curl.

## Phase 4 — Chunking and Relevance Ranking

### 4.1 Create chunker
Create:

```text
geo_lm/extraction/chunker.py
```

- [ ] Split by page and target token-ish size.
- [ ] Keep page ranges.
- [ ] Keep source document ID.
- [ ] Create deterministic chunk IDs.

Done when: extracted pages produce chunk list.

### 4.2 Create relevance scorer
Create:

```text
geo_lm/extraction/relevance.py
```

Score on:

- [ ] geology keywords.
- [ ] NYC formation names.
- [ ] numeric depth/unit patterns.
- [ ] dip/strike patterns.
- [ ] location names: Manhattan, Bronx, Queens.

Done when: chunks are sorted by score and saved.

### 4.3 Save chunks
Artifact:

```text
data/runs/{run_id}/ranked_chunks.json
```

Done when: run folder contains ranked chunk artifact.

## Phase 5 — Extraction Schema and Prompting

### 5.1 Define Pydantic schemas
Create:

```text
geo_lm/extraction/schemas.py
```

Models:

- [ ] `EvidenceRef`.
- [ ] `Formation`.
- [ ] `Contact`.
- [ ] `Structure`.
- [ ] `ExtractionResult`.
- [ ] `ValidationReport`.

Done when: schema can validate a fixture JSON.

### 5.2 Create prompt templates
Create:

```text
geo_lm/prompts/nyc_geology_extraction.md
geo_lm/prompts/repair_extraction.md
```

Requirements:

- [ ] Ask for JSON only.
- [ ] Include schema.
- [ ] Require evidence quotes.
- [ ] Require units.
- [ ] Tell model to use `null` if unknown.
- [ ] Avoid hallucinating coordinates.

Done when: prompt text is versioned and loaded by code.

### 5.3 Implement extraction call
Create:

```text
geo_lm/extraction/llm_extractor.py
```

- [ ] Select top chunks.
- [ ] Call Ollama.
- [ ] Parse JSON.
- [ ] Save raw output.
- [ ] Validate schema.
- [ ] Return structured extraction.

Done when: fixture chunk produces parsed extraction.

### 5.4 Repair loop
- [ ] On validation failure, generate repair prompt.
- [ ] Include validation errors.
- [ ] Retry up to max.
- [ ] Save every attempt.
- [ ] Fail gracefully with artifact logs.

Done when: invalid fixture is repaired or returns useful failure report.

## Phase 6 — DSL Generation and Validation

### 6.1 Normalize geology entities
Create:

```text
data/fixtures/nyc_geology_glossary.json
geo_lm/domain/normalization.py
```

Include:

- [ ] Manhattan Schist.
- [ ] Inwood Marble.
- [ ] Fordham Gneiss.
- [ ] Walloomsac Formation.
- [ ] Hartland Formation.
- [ ] Ravenswood Granodiorite.

Done when: aliases normalize to canonical names.

### 6.2 Generate DSL
Create:

```text
geo_lm/dsl/build_from_extraction.py
```

- [ ] Convert formations to `ROCK` elements.
- [ ] Convert relative ordering/contact facts to DSL events where possible.
- [ ] Include comments or metadata separately, not in invalid DSL.
- [ ] Write `data/runs/{run_id}/geology.dsl`.

Done when: fixture extraction writes DSL file.

### 6.3 Validate DSL
- [ ] Use upstream parser if available.
- [ ] Add parser test using fixture DSL.
- [ ] Save `validation_report.json`.

Done when: generated fixture DSL validates.

## Phase 7 — GemPy Input Builder

### 7.1 Define intermediate model constraints
Create:

```text
geo_lm/modeling/constraints.py
```

Represent:

- [ ] formations.
- [ ] surface/contact points.
- [ ] orientations/dips.
- [ ] source: extracted/inferred/fixture.
- [ ] confidence/evidence links.

Done when: constraints can be serialized to JSON.

### 7.2 Build constraints from extraction/DSL
Create:

```text
geo_lm/modeling/constraint_builder.py
```

- [ ] Map canonical formation names.
- [ ] Generate minimal surface points if georeferencing is incomplete.
- [ ] Label inferred points honestly.
- [ ] Write `gempy_inputs.json`.

Done when: fixture DSL generates GemPy inputs.

### 7.3 Decide AOI bounds
Use Manhattan + Bronx + Queens v1.

- [ ] Set initial bbox in config.
- [ ] Use coarse grid for first pass.
- [ ] Document units and CRS.

Done when: GemPy runner has deterministic bounds.

## Phase 8 — GemPy Runner and Mesh Export

### 8.1 Create GemPy runner
Create:

```text
geo_lm/modeling/gempy_runner.py
```

- [ ] Initialize model.
- [ ] Load formations.
- [ ] Load points/orientations.
- [ ] Compute model.
- [ ] Return surfaces or vertices.

Done when: fixture inputs complete a GemPy smoke run.

### 8.2 Export mesh
Create:

```text
geo_lm/modeling/export_mesh.py
```

- [ ] Export `.gltf` or `.glb`.
- [ ] Use `trimesh` if needed.
- [ ] Store under `data/exports/{run_id}.gltf`.
- [ ] Write fallback `sample.gltf`.

Done when: file opens in a generic viewer or loads from local static URL.

### 8.3 Static serving
- [ ] Mount `data/exports` in FastAPI at `/static/exports`.
- [ ] Confirm browser can fetch `/static/exports/{run_id}.gltf`.

Done when: Part 1 can use returned `gltf_path`.

## Phase 9 — Scalar Field Export

### 9.1 Implement real or stub field exporter
Create:

```text
geo_lm/modeling/export_field.py
```

Output:

```text
data/fields/depth.npz
data/fields/depth_meta.json
```

- [ ] `grid` float32.
- [ ] `x` float32.
- [ ] `y` float32.
- [ ] optional `mask`.
- [ ] metadata includes CRS, bbox, resolution, units, source, run ID.

Done when: `numpy.load("data/fields/depth.npz")` shows required keys.

### 9.2 Stub fallback field
- [ ] Create deterministic smooth field if GemPy field unavailable.
- [ ] Mark `source: "stub"` in meta.
- [ ] Ensure Part 3 can build against it.

Done when: field export never blocks the demo pipeline.

## Phase 10 — Run Orchestration API

### 10.1 Create run service
Create:

```text
geo_lm/runs/run_service.py
```

Stages:

1. Create run ID.
2. Load/extract documents.
3. Chunk and rank.
4. LLM extraction.
5. Validate/repair.
6. Generate DSL.
7. Build GemPy inputs.
8. Run GemPy.
9. Export mesh.
10. Export field.
11. Write manifest.

Done when: service works in fixture mode.

### 10.2 Create API router
Create:

```text
api/routers/runs.py
```

Endpoints:

- [ ] `POST /api/run`
- [ ] `GET /api/run/{run_id}`

Done when: Swagger docs show both endpoints.

### 10.3 Add manifest writer
Artifact:

```text
data/runs/{run_id}/run_manifest.json
```

Fields:

- [ ] run ID.
- [ ] timestamps.
- [ ] documents.
- [ ] model.
- [ ] provider.
- [ ] used fixture fallback.
- [ ] artifacts.
- [ ] warnings.
- [ ] errors.

Done when: every run writes manifest, success or failure.

## Phase 11 — Fixture and Demo Reliability

### 11.1 Create fixture extraction
Create:

```text
data/fixtures/demo_extraction.json
```

Must include:

- [ ] at least 3 formations.
- [ ] at least 2 contacts/depth facts.
- [ ] evidence strings.

Done when: schema validates.

### 11.2 Create fixture DSL
Create:

```text
data/fixtures/demo_geology.dsl
```

Done when: parser validates it.

### 11.3 Create fixture run command
Add one command:

```bash
python scripts/run_fixture_demo.py
```

or:

```bash
make run-demo
```

It should:

- [ ] bypass live PDF extraction if requested.
- [ ] generate `.gltf`.
- [ ] generate `depth.npz`.
- [ ] write manifest.

Done when: a clean machine can run fixture mode in under 15 seconds after setup.

## Phase 12 — Tests

### 12.1 Unit tests
Add tests for:

- [ ] config loading.
- [ ] Ollama client mocked success/failure.
- [ ] PDF extraction against a tiny test PDF or mocked file.
- [ ] chunk ranking.
- [ ] schema validation.
- [ ] repair prompt creation.
- [ ] glossary normalization.
- [ ] DSL generation.
- [ ] field export schema.

### 12.2 Integration tests
Add:

- [ ] fixture run end-to-end.
- [ ] `/api/run` fixture mode.
- [ ] static mesh serving.

### 12.3 Manual tests
Checklist:

- [ ] Ollama running.
- [ ] one PDF extraction.
- [ ] one live LLM extraction.
- [ ] one live or fixture GemPy model.
- [ ] one frontend mesh fetch URL.

Done when: tests pass and manual checklist is documented in README.

## Phase 13 — Handoff to Part 1 and Part 3

### 13.1 Handoff to Part 1
Provide:

- [ ] `POST /api/run` schema.
- [ ] sample response JSON.
- [ ] stable `.gltf` URL.
- [ ] sample `.gltf` asset.
- [ ] CORS config instructions.

Done when: frontend can render a mesh without asking Part 2 for code changes.

### 13.2 Handoff to Part 3
Provide:

- [ ] `data/fields/depth.npz`.
- [ ] `data/fields/depth_meta.json`.
- [ ] explanation of units/CRS.
- [ ] whether source is `gempy`, `stub`, or `fixture`.

Done when: Part 3 can load the field and run its own scripts.

## Phase 14 — README Updates
Update backend README with:

- [ ] setup commands.
- [ ] venv instructions.
- [ ] Ollama setup.
- [ ] model pull.
- [ ] env vars.
- [ ] run server.
- [ ] run fixture demo.
- [ ] run real PDF demo.
- [ ] API contracts.
- [ ] troubleshooting.

Done when: another teammate can follow README from scratch.

## Phase 15 — Demo-Day Preparation

### 15.1 Cache artifacts
- [ ] Run full demo once.
- [ ] Save known-good run ID.
- [ ] Confirm `data/exports/{run_id}.gltf`.
- [ ] Confirm `data/fields/depth.npz`.
- [ ] Confirm manifest.

### 15.2 Smoke test
Run:

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/llm/health
curl http://localhost:8000/api/run/{run_id}
```

- [ ] All return successful JSON.

### 15.3 Fallback plan
Prepare:

- [ ] fixture run command.
- [ ] sample `.gltf`.
- [ ] stub `depth.npz`.
- [ ] screenshots/video if live run fails.

Done when: demo can continue even if Ollama or GemPy fails live.

## Suggested Implementation Order
If time is tight, prioritize:

1. Venv + Ollama health.
2. Fixture run pipeline.
3. Static `.gltf` export path.
4. Stub `depth.npz`.
5. `/api/run`.
6. Real PDF extraction.
7. LLM extraction.
8. Real GemPy model.
9. Polish and tests.

This order lets the frontend and Part 3 integrate early.

## Definition of Done for Part 2
Part 2 is done when:

- [ ] Backend starts locally with `.venv`.
- [ ] No cloud LLM key is needed.
- [ ] Ollama health endpoint works.
- [ ] Fixture run writes all artifacts.
- [ ] At least one real PDF extraction works.
- [ ] `/api/run` returns stable response JSON.
- [ ] Mesh file is fetchable via HTTP.
- [ ] `depth.npz` and `depth_meta.json` match schema.
- [ ] Tests for critical paths pass.
- [ ] README setup is complete.

## Troubleshooting Checklist

### Ollama not reachable
- [ ] Run `curl http://localhost:11434/api/tags`.
- [ ] Start `ollama serve`.
- [ ] Confirm no firewall/VPN is blocking localhost.

### Model missing
- [ ] Run `ollama list`.
- [ ] Pull model again: `ollama pull llama3.1:8b`.
- [ ] Check `.env` model name matches `ollama list`.

### Python import failures
- [ ] Confirm venv active.
- [ ] Reinstall dependencies.
- [ ] Check Python version.
- [ ] Avoid mixing Poetry virtualenv and manual `.venv`.

### LLM output invalid
- [ ] Lower temperature.
- [ ] Reduce chunk size.
- [ ] Strengthen prompt with schema.
- [ ] Use repair loop.
- [ ] Use fixture fallback.

### GemPy fails
- [ ] Reduce model complexity.
- [ ] Use fixture constraints.
- [ ] Export a simplified `trimesh` surface.
- [ ] Keep warnings visible in manifest.

### Frontend cannot load mesh
- [ ] Open glTF URL directly in browser.
- [ ] Check CORS.
- [ ] Check file exists under `data/exports`.
- [ ] Try copying asset to Next `public/exports`.

## Daily Milestones

### Day 1
- [ ] Venv.
- [ ] Ollama.
- [ ] FastAPI health.
- [ ] Fixture schemas.
- [ ] Stub `/api/run`.

### Day 2
- [ ] PDF extraction.
- [ ] Chunk ranking.
- [ ] Ollama extraction.
- [ ] Validation/repair.
- [ ] Fixture DSL.

### Day 3
- [ ] GemPy runner.
- [ ] Mesh export.
- [ ] Field export.
- [ ] Static serving.
- [ ] Part 1/3 handoff.

### Final Polish
- [ ] Real report cached run.
- [ ] Tests.
- [ ] README.
- [ ] Demo fallback.
