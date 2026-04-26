/**
 * Builds a single Polygon: exterior = padded rectangle around NYC, interiors = borough outlines.
 * MapLibre draws the fill only outside the holes, so the basemap shows only within the 5 boroughs.
 *
 * Run from apps/web: node scripts/build-nyc-outside-mask.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.join(__dirname, "..");
const src = path.join(webRoot, "public/layers/boroughs_nyc.geojson");
const out = path.join(webRoot, "public/layers/nyc_outside_mask.geojson");

/** Signed area × 2 for closed ring [lon, lat]; positive ⇒ CCW (x east, y north). */
function ringDoubleArea(ring) {
  let a = 0;
  const n = ring.length;
  if (n < 3) return 0;
  for (let i = 0; i < n - 1; i++) {
    a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  }
  return a;
}

function orientRing(ring, wantCCW) {
  let open = ring.slice();
  const first = open[0];
  const last = open[open.length - 1];
  if (first[0] === last[0] && first[1] === last[1]) open = open.slice(0, -1);
  if (open.length < 3) return [...open, open[0]];
  const closed = [...open, open[0]];
  const ccw = ringDoubleArea(closed) > 0;
  if (ccw !== wantCCW) open = open.slice().reverse();
  return [...open, open[0]];
}

function collectExteriorRings(geometry) {
  const rings = [];
  if (geometry.type === "Polygon") {
    rings.push(geometry.coordinates[0]);
  } else if (geometry.type === "MultiPolygon") {
    for (const poly of geometry.coordinates) {
      rings.push(poly[0]);
    }
  }
  return rings;
}

const fc = JSON.parse(fs.readFileSync(src, "utf8"));
const holeRingsRaw = [];
for (const f of fc.features) {
  holeRingsRaw.push(...collectExteriorRings(f.geometry));
}

// Padded frame: must cover MapView `NYC_PAN_MAX_BOUNDS` so corners are not unmasked.
const outer = orientRing(
  [
    [-74.56, 40.21],
    [-73.14, 40.21],
    [-73.14, 41.19],
    [-74.56, 41.19],
  ],
  true,
);

const holes = holeRingsRaw.map((r) => orientRing(r, false));

const feature = {
  type: "Feature",
  properties: { name: "nyc_outside_mask" },
  geometry: {
    type: "Polygon",
    coordinates: [outer, ...holes],
  },
};

fs.writeFileSync(
  out,
  JSON.stringify({ type: "FeatureCollection", features: [feature] }),
);
console.log(`Wrote ${out} (${holes.length} interior rings + 1 exterior).`);
