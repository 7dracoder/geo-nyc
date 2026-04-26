/** Minimal GeoJSON types for borough hit-testing (no @types/geojson dependency). */

type Ring = number[][];

type PolygonGeom = { type: "Polygon"; coordinates: Ring[] };

/** Each entry is one polygon: [exterior ring, ...hole rings]. */
type MultiPolygonGeom = { type: "MultiPolygon"; coordinates: Ring[][] };

export type BoroughFeatureCollection = {
  type: "FeatureCollection";
  features: { geometry: PolygonGeom | MultiPolygonGeom }[];
};

function inRing(lng: number, lat: number, ring: Ring): boolean {
  let open = ring;
  if (ring.length >= 2) {
    const a = ring[0];
    const b = ring[ring.length - 1];
    if (a[0] === b[0] && a[1] === b[1]) open = ring.slice(0, -1);
  }
  const n = open.length;
  if (n < 3) return false;
  let inside = false;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = open[i][0];
    const yi = open[i][1];
    const xj = open[j][0];
    const yj = open[j][1];
    const denom = yj - yi;
    if (Math.abs(denom) < 1e-14) continue;
    const intersect =
      (yi > lat) !== (yj > lat) && lng < ((xj - xi) * (lat - yi)) / denom + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/** Point in polygon with holes: rings[0] exterior, rings[1..] holes. */
function inPolygonRings(lng: number, lat: number, rings: Ring[]): boolean {
  if (!inRing(lng, lat, rings[0])) return false;
  for (let h = 1; h < rings.length; h++) {
    if (inRing(lng, lat, rings[h])) return false;
  }
  return true;
}

function inPolygonGeom(lng: number, lat: number, g: PolygonGeom): boolean {
  return inPolygonRings(lng, lat, g.coordinates);
}

function inMultiPolygonGeom(lng: number, lat: number, g: MultiPolygonGeom): boolean {
  for (const polygon of g.coordinates) {
    if (inPolygonRings(lng, lat, polygon)) return true;
  }
  return false;
}

/** True if lng/lat lies inside any of the five borough polygons. */
export function isLngLatInsideNycFiveBoroughs(
  lng: number,
  lat: number,
  fc: BoroughFeatureCollection,
): boolean {
  for (const f of fc.features) {
    const g = f.geometry;
    if (g.type === "Polygon") {
      if (inPolygonGeom(lng, lat, g)) return true;
    } else if (g.type === "MultiPolygon") {
      if (inMultiPolygonGeom(lng, lat, g)) return true;
    }
  }
  return false;
}
