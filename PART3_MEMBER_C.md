# Part 3 Member C Implementation Notes

Part 3 implementation for the Urban Subsurface AI blueprint (Member C): NYC Open Data ingestion, field smoothing, and optimization API.

## What is implemented

- `scripts/fetch_open_data.py`
  - Downloads borough boundaries from NYC Open Data.
  - Filters borough codes to `1,2,3` by default (Manhattan, Bronx, Brooklyn).
  - Dissolves AOI and clips/simplifies additional contextual layers.
  - Pulls real contextual layers including hurricane evacuation zones and water-included borough boundaries.
  - Writes `web/public/layers/*.geojson` and `web/public/layers/manifest.json`.
- `scripts/build_field.py`
  - Uses `data/fields/depth.npz` when real Part 2 depth fields are available.
  - If depth is missing/stub, generates a real-data proxy field from AOI + hazard layers (no synthetic hardcoded surface).
  - Fills sparse values and smooths the field.
  - Writes `data/fields/cost_grid.npz`, `data/fields/cost_raster_meta.json`, and `web/public/layers/depth_contours.geojson`.
- FastAPI routes:
  - `GET /api/layers` reads and returns manifest layers.
  - `POST /api/optimize` performs fast 1D grid-search optimization from precomputed field data (no GemPy call at runtime).
  - `POST /api/optimize/reload` clears cache and reloads field files.
  - `GET /health` for readiness checks.

## File contracts (Part 3)

- `web/public/layers/manifest.json`
- `web/public/layers/*.geojson`
- `data/fields/depth.npz` + `data/fields/depth_meta.json`
- `data/fields/cost_grid.npz` + `data/fields/cost_raster_meta.json`
- `data/source_pdfs/sources.json` + `data/source_pdfs/*.pdf` (inputs for Part 2 ingestion)

## Install

```bash
pip install -r requirements.txt
```

## Run scripts

```bash
python scripts/fetch_open_data.py --output-dir web/public --boro-codes 1,2,3
python scripts/build_field.py
```

Real PDF inputs for Part 2 ingestion live in:

```bash
data/source_pdfs/sources.json
```

Queens swap:

```bash
python scripts/fetch_open_data.py --output-dir web/public --boro-codes 1,2,4
```

## Run API

```bash
uvicorn api.main:app --reload --port 8000
```

## Example API calls

```bash
curl http://127.0.0.1:8000/api/layers
curl -X POST http://127.0.0.1:8000/api/optimize -H "Content-Type: application/json" -d "{\"mode\":\"geothermal\",\"params\":{\"d_min\":5,\"d_max\":60}}"
```
