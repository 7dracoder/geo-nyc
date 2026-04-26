export type BBoxLngLat = [number, number, number, number]; // west, south, east, north

function extend(b: BBoxLngLat, lng: number, lat: number): void {
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
  b[0] = Math.min(b[0], lng);
  b[1] = Math.min(b[1], lat);
  b[2] = Math.max(b[2], lng);
  b[3] = Math.max(b[3], lat);
}

function walkCoords(value: unknown, b: BBoxLngLat): void {
  if (!Array.isArray(value) || value.length === 0) return;
  if (typeof value[0] === "number" && typeof value[1] === "number") {
    extend(b, value[0] as number, value[1] as number);
    return;
  }
  for (const item of value) {
    walkCoords(item, b);
  }
}

function walkGeometry(geom: unknown, b: BBoxLngLat): void {
  if (!geom || typeof geom !== "object") return;
  const g = geom as { type?: string; coordinates?: unknown; geometries?: unknown[] };
  if (g.type === "GeometryCollection" && Array.isArray(g.geometries)) {
    for (const sub of g.geometries) walkGeometry(sub, b);
    return;
  }
  if ("coordinates" in g && g.coordinates !== undefined) {
    walkCoords(g.coordinates, b);
  }
}

/** Returns null if no finite coordinates were found. */
export function bboxFromGeoJSON(data: unknown): BBoxLngLat | null {
  const b: BBoxLngLat = [Infinity, Infinity, -Infinity, -Infinity];
  if (!data || typeof data !== "object") return null;

  const d = data as {
    type?: string;
    features?: unknown[];
    geometry?: unknown;
    coordinates?: unknown;
  };

  if (d.type === "FeatureCollection" && Array.isArray(d.features)) {
    for (const f of d.features) {
      if (f && typeof f === "object" && "geometry" in f) {
        walkGeometry((f as { geometry: unknown }).geometry, b);
      }
    }
  } else if (d.type === "Feature" && d.geometry) {
    walkGeometry(d.geometry, b);
  } else {
    walkGeometry(data, b);
  }

  if (!Number.isFinite(b[0]) || !Number.isFinite(b[1])) return null;
  if (b[2] < b[0] || b[3] < b[1]) return null;
  return b;
}

export function unionBBox(a: BBoxLngLat, b: BBoxLngLat): BBoxLngLat {
  return [
    Math.min(a[0], b[0]),
    Math.min(a[1], b[1]),
    Math.max(a[2], b[2]),
    Math.max(a[3], b[3]),
  ];
}

/** Intersection of two axis-aligned boxes; null if disjoint or degenerate. */
export function intersectBBox(a: BBoxLngLat, b: BBoxLngLat): BBoxLngLat | null {
  const w = Math.max(a[0], b[0]);
  const s = Math.max(a[1], b[1]);
  const e = Math.min(a[2], b[2]);
  const n = Math.min(a[3], b[3]);
  if (e <= w || n <= s) return null;
  return [w, s, e, n];
}

/** `[[west,south],[east,north]]` → bbox tuple */
export function bboxFromLngLatBounds(
  sw: [number, number],
  ne: [number, number],
): BBoxLngLat {
  return [sw[0], sw[1], ne[0], ne[1]];
}
