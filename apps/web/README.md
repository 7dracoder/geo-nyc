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
- **What-if:** Debounced calls to `POST /api/optimize` when `NEXT_PUBLIC_API_BASE_URL` is set; otherwise a local mock. Add `?mock=1` to force the mock even if a base URL is set (`src/lib/api.ts`). In the browser the real URL is same-origin `/geo-nyc-proxy/...` (rewritten by Next to your backend) so cross-origin CORS is avoided on Vercel.
- **Part 3 overlays:** `AppShell` loads the manifest via `fetchDisplayableManifestLayers` (`src/lib/manifestLayers.ts`). The **Layers** sidebar toggles each overlay; **Fit to map** zooms to the union of bounds of visible layers, **clipped to the five-borough frame** (same bounds as the initial map fit). Overlays are drawn **under** the outside-borough mask so extra AOI geometry does not sit on top of the grey “non‑NYC” fill.

## Subsurface 3D (`SubsurfaceViewer.tsx`)

- Client-only R3F canvas in a bottom dock; hover expands the panel (see `src/app/globals.css` `.subsurface-*`).
- **Model URL** (`resolveGltfUrl` in `src/lib/gltfAsset.ts`): order is `NEXT_PUBLIC_GLTF_URL` (full override) → optional **`NEXT_PUBLIC_MESH_RUN_ID`** plus `GET /geo-nyc-proxy/api/run/{id}` to resolve the run’s mesh → else newest runs from `GET /geo-nyc-proxy/api/runs` (prefers `status: succeeded`) probing **`/geo-nyc-proxy/static/exports/{run_id}/model.glb`** (or the mesh filename from the manifest) → legacy `static/exports/sample.glb` → `public/exports/sample.glb` → **Khronos Duck** so the dock always has geometry.
- **DSL vs GemPy:** the subsurface **`.glb`** is produced by the Python run pipeline’s mesh export step (same artifact path regardless of engine). **GemPy** is optional on the API host (`GEO_NYC_ENABLE_GEMPY`); without it the backend may still emit a mesh from RBF / synthetic / stub paths. The web app does not call GemPy directly; it only loads whatever mesh URL the run manifest describes (proxied through `/geo-nyc-proxy`).

## Layout and styling

- App shell: `src/components/AppShell.tsx` — header, sidebar, map + 3D dock.
- Global tokens and map chrome (attribution, zoom controls): `src/app/globals.css` (`:root`, `.map-shell`, …).
- Fonts: IBM Plex Sans + Source Serif 4 (`src/app/layout.tsx`).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Upstream FastAPI origin (`https://…`, optional trailing slash). Used at **build** time for `next.config.ts` rewrites from `/geo-nyc-proxy/*` to this host. Invalid or host-less values are ignored (avoids Vercel `DNS_HOSTNAME_EMPTY` from bad rewrites). If empty or invalid, what-if uses the mock and overlays use only `public/layers/manifest.json`. |

| `NEXT_PUBLIC_GLTF_URL` | Optional full URL to a `.glb` for the 3D dock. Highest priority when set. |
| `NEXT_PUBLIC_MESH_RUN_ID` | Optional run id (e.g. `r_20260101000000_abcdef12`). When set and `NEXT_PUBLIC_API_BASE_URL` is valid, the dock loads that run’s mesh from the API manifest instead of guessing from `/api/runs`. |

When neither override is set but the API proxy is configured, the app picks the newest run that still has a mesh file, then falls back to sample paths and the Khronos duck.

Use the **same** origin for both sides when tunneling (e.g. `https://your-name.ngrok-free.dev`): set `NEXT_PUBLIC_API_BASE_URL` on Vercel / `.env.local`, and set `GEO_NYC_PUBLIC_BASE_URL` on the FastAPI host so manifest URLs for `/static/exports/...` match what the browser can reach.

Free ngrok may return an HTML interstitial on programmatic requests; `src/proxy.ts` adds `ngrok-skip-browser-warning` for `/geo-nyc-proxy/*` so rewrites get JSON from the API.

The Python API does not need to list your Vercel domain in CORS for these calls (traffic is Vercel → upstream). CORS on the backend still matters for other clients that hit ngrok directly from the browser.

## Project layout (selected)

```
src/
  app/              # Next app router, layout, globals.css
  components/      # AppShell, MapView, LayerPanel, ManifestOverlays, AddressSearch, WhatIfPanel, SubsurfaceViewer
  lib/              # geocode, api, manifestLayers, geojson-bbox, gltfAsset, nyc-borough-hit, positron-label-cleanup
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
