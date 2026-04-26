import type { MapPickLocation } from "@/types/map-pick";

/** Nominatim viewbox: min lon, max lat, max lon, min lat (NYC metro + padding). */
const NYC_VIEWBOX = "-74.35,40.95,-73.58,40.45";

export type GeocodeHit = MapPickLocation & { displayName: string };

type SearchOpts = {
  limit: number;
  /** If true, only results inside viewbox; if false, viewbox biases ranking only. */
  bounded: boolean;
};

async function nominatimSearch(
  query: string,
  opts: SearchOpts,
  signal?: AbortSignal,
): Promise<GeocodeHit[]> {
  const q = query.trim();
  if (!q) return [];

  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.searchParams.set("q", q);
  url.searchParams.set("format", "json");
  url.searchParams.set("limit", String(opts.limit));
  url.searchParams.set("addressdetails", "0");
  url.searchParams.set("viewbox", NYC_VIEWBOX);
  url.searchParams.set("bounded", opts.bounded ? "1" : "0");
  url.searchParams.set("countrycodes", "us");

  const res = await fetch(url.toString(), {
    signal,
    headers: {
      Accept: "application/json",
      "User-Agent": "geoNYC/0.1 (local dev; contact: none)",
    },
  });

  if (!res.ok) {
    throw new Error(`Geocoder returned ${res.status}. Try again later.`);
  }

  const rows = (await res.json()) as {
    lat: string;
    lon: string;
    display_name?: string;
  }[];

  return rows
    .map((top) => {
      const lat = Number(top.lat);
      const lng = Number(top.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
      return {
        lng,
        lat,
        displayName: top.display_name ?? q,
      } satisfies GeocodeHit;
    })
    .filter((x): x is GeocodeHit => x != null);
}

const MIN_SUGGEST_CHARS = 3;

/** Typeahead / dropdown: biased to NYC viewbox, not strictly bounded (more partial matches). */
export async function searchAddressSuggestions(
  query: string,
  signal?: AbortSignal,
): Promise<GeocodeHit[]> {
  const q = query.trim();
  if (q.length < MIN_SUGGEST_CHARS) return [];
  return nominatimSearch(q, { limit: 8, bounded: false }, signal);
}

/**
 * Forward geocode via OpenStreetMap Nominatim (free; use sparingly in production).
 * Strict to NYC viewbox for a single best hit.
 * @see https://operations.osmfoundation.org/policies/nominatim/
 */
export async function geocodeAddress(query: string): Promise<GeocodeHit> {
  const q = query.trim();
  if (!q) {
    throw new Error("Enter an address or place name.");
  }

  const hits = await nominatimSearch(q, { limit: 5, bounded: true });
  if (!hits.length) {
    throw new Error("No results in the NYC area. Try a different search.");
  }
  return hits[0];
}
