import { apiBase } from "@/lib/api";

export type ManifestLayer = {
  id: string;
  title: string;
  type: "fill" | "line";
  geojson_path: string;
  opacity: number;
  legend_color: string;
  source_url?: string;
  download_date?: string;
};

type Manifest = {
  layers: ManifestLayer[];
};

const SKIP_IDS: ReadonlySet<string> = new Set([
  "boroughs",
  "aoi",
  "borough_boundaries_water",
]);

const STATIC_MANIFEST_URL = "/layers/manifest.json";

function resolveManifestGeojsonUrl(geojsonPath: string, origin: string): string {
  if (geojsonPath.startsWith("http://") || geojsonPath.startsWith("https://")) {
    return geojsonPath;
  }
  if (geojsonPath.startsWith("/layers/")) {
    const name = geojsonPath.slice("/layers/".length);
    return `${origin}/static/layers/${name}`;
  }
  if (geojsonPath.startsWith("/")) {
    return `${origin}${geojsonPath}`;
  }
  return geojsonPath;
}

/**
 * Loads Part 3 manifest layers suitable for the map (API or static),
 * excluding IDs that duplicate borough chrome.
 */
export async function fetchDisplayableManifestLayers(
  signal?: AbortSignal,
): Promise<ManifestLayer[]> {
  const base = apiBase();

  const loadRaw = async (): Promise<ManifestLayer[]> => {
    if (base) {
      try {
        const r = await fetch(`${base}/api/layers`, { signal });
        if (!r.ok) throw new Error(`api/layers ${r.status}`);
        const m = (await r.json()) as Manifest;
        return (m.layers ?? []).map((layer) => ({
          ...layer,
          geojson_path: resolveManifestGeojsonUrl(layer.geojson_path, base),
        }));
      } catch {
        // fall through to static
      }
    }
    const r = await fetch(STATIC_MANIFEST_URL, { signal });
    if (!r.ok) throw new Error(`manifest ${r.status}`);
    const m = (await r.json()) as Manifest;
    return m.layers ?? [];
  };

  const raw = await loadRaw();
  return raw.filter(
    (l) => l && !SKIP_IDS.has(l.id) && (l.type === "fill" || l.type === "line"),
  );
}
