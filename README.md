# geo-nyc

Part 3 implementation for the Urban Subsurface AI blueprint (Member C): NYC Open Data ingestion, field smoothing, and optimization API.

## What is implemented

- `scripts/fetch_open_data.py`
  - Downloads borough boundaries from NYC Open Data.
  - Filters borough codes to `1,2,3` by default (Manhattan, Bronx, Brooklyn).
  - Dissolves AOI and clips/simplifies additional contextual layers.
  - Writes `web/public/layers/*.geojson` and `web/public/layers/manifest.json`.
- `scripts/build_field.py`
  - Reads `data/fields/depth.npz`.
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

## Install

```bash
pip install -r requirements.txt
```

## Run scripts

```bash
python scripts/fetch_open_data.py --output-dir web/public --boro-codes 1,2,3
python scripts/build_field.py
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