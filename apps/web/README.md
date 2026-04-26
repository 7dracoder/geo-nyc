# geoNYC (`apps/web`)

Next.js app: NYC-focused map, optional subsurface GLB preview, address search, and a small “what-if” optimizer panel.

## Requirements

- Node.js 20+ (or the repo’s portable Node under `.tools/` if you use that layout)
- npm

## Run locally

```bash
cd apps/web
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

```bash
npm run build   # production build
npm run start   # after build
npm run lint    # ESLint
```

## Map (`MapView.tsx`)

- **Basemap:** [Carto Positron](https://basemaps.cartocdn.com/gl/positron-gl-style/style.json) (fixed style, not user-selectable).
- **View:** Camera is limited to a padded NYC frame (`maxBounds`). Initial view fits the five-borough bounding box.
- **Outside-borough mask:** `public/layers/nyc_outside_mask.geojson` is a polygon with holes matching official borough land. Everything outside those holes is covered with a solid fill so the road map is only visible **inside** the five boroughs.
- **Borough outline:** `public/layers/boroughs_nyc.geojson` is always shown as a line layer (no layer toggles in the UI).
- **Borough names:** `public/layers/borough_labels.geojson` supplies point labels; opacity fades out as you zoom in so street detail dominates.
- **Basemap labels:** Regional place and water name symbol layers from Positron are hidden in code so labels are not clipped awkwardly at the mask edge. Street names and housenumbers stay on.
- **Picking:** Map clicks call `onPick` only when the point is **inside** the borough polygons (same GeoJSON as the boundary). Clicks on the masked area outside NYC do nothing.

### Regenerate the outside mask

If you replace `boroughs_nyc.geojson`, rebuild the mask so holes stay aligned:

```bash
cd apps/web
npm run layers:mask
```

Script: `scripts/build-nyc-outside-mask.mjs`. It writes `public/layers/nyc_outside_mask.geojson`. The mask’s **outer rectangle** must stay in sync with `NYC_PAN_MAX_BOUNDS` in `src/components/MapView.tsx` (see comments there and in the script).

## Sidebar

- **Address:** Nominatim (`src/lib/geocode.ts`) with NYC-biased search; autocomplete and submit. Attribution: OpenStreetMap link in the panel.
- **What-if:** Debounced calls to `POST /api/optimize` when `NEXT_PUBLIC_API_BASE_URL` is set; otherwise a local mock. Add `?mock=1` to force the mock even if a base URL is set (`src/lib/api.ts`).
- **Part 3 overlays:** `ManifestOverlays.tsx` loads the layer list from `GET {API}/api/layers` when `NEXT_PUBLIC_API_BASE_URL` is set (GeoJSON URLs are rewritten to `{API}/static/layers/...`). If that fails or the env is unset, it falls back to static `public/layers/manifest.json`.

## Subsurface 3D (`SubsurfaceViewer.tsx`)

- Client-only R3F canvas in a bottom dock; hover expands the panel (see `src/app/globals.css` `.subsurface-*`).
- Default asset: `public/exports/sample.glb` (ensure this file exists for the preview to load).

## Layout and styling

- App shell: `src/components/AppShell.tsx` — header, sidebar, map + 3D dock.
- Global tokens and map chrome (attribution, zoom controls): `src/app/globals.css` (`:root`, `.map-shell`, …).
- Fonts: IBM Plex Sans + Source Serif 4 (`src/app/layout.tsx`).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Origin for the FastAPI backend (no trailing slash). Powers `POST /api/optimize`, `GET /api/layers` (map overlays), and related static GeoJSON under `/static/layers/`. If empty, what-if uses the mock and overlays read only `public/layers/manifest.json`. |

Backend CORS must allow your web origin (set `GEO_NYC_CORS_ORIGINS` and/or `GEO_NYC_CORS_ORIGIN_REGEX` when the Python API is not only on localhost). See `geo_nyc/config.py`.

## Project layout (selected)

```
src/
  app/              # Next app router, layout, globals.css
  components/      # AppShell, MapView, AddressSearch, WhatIfPanel, SubsurfaceViewer
  lib/              # geocode, api (optimize), nyc-borough-hit, positron-label-cleanup
  types/            # map-pick, optimize
public/
  layers/           # boroughs_nyc.geojson, nyc_outside_mask.geojson, borough_labels.geojson
  exports/          # sample.glb (subsurface preview)
scripts/
  build-nyc-outside-mask.mjs
```

There is **no** `public/layers/manifest.json` anymore; map overlays are wired directly in `MapView`.

## Data notes

- **Borough boundaries:** NYC Open Data borough boundaries (simplified GeoJSON in `public/layers/boroughs_nyc.geojson`). Regenerate the mask after changing that file.
- **Map tiles & glyphs:** Carto / OSM contributors per MapLibre attribution on the map.

## License / attribution

Respect [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) and Carto/OSM terms for basemaps. Use a proper `User-Agent` identifying your app in `geocode.ts` if you change hosts or traffic patterns.
