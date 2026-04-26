"use client";

import { useEffect, useState } from "react";
import { Layer, Source } from "react-map-gl/maplibre";

/** Shape returned by `GET /layers/manifest.json` (Part 3 data layer). */
type ManifestLayer = {
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

/**
 * IDs we deliberately skip so they don't clash with the hardcoded
 * borough mask / boundary already rendered by `MapView`.
 *
 * - `boroughs`: Part 3 AOI subset (e.g. just MN+BX+BK) — duplicates
 *   `boroughs_nyc.geojson` line we already draw.
 * - `aoi`: Part 3 AOI fill — would obscure the basemap inside boroughs.
 * - `borough_boundaries_water`: visually duplicates the borough outline.
 */
const SKIP_IDS: ReadonlySet<string> = new Set([
  "boroughs",
  "aoi",
  "borough_boundaries_water",
]);

const MANIFEST_URL = "/layers/manifest.json";

export function ManifestOverlays() {
  const [layers, setLayers] = useState<ManifestLayer[]>([]);

  useEffect(() => {
    let cancelled = false;
    const ac = new AbortController();
    fetch(MANIFEST_URL, { signal: ac.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error(`manifest ${r.status}`);
        return (await r.json()) as Manifest;
      })
      .then((m) => {
        if (cancelled) return;
        const filtered = (m.layers ?? []).filter(
          (l) => l && !SKIP_IDS.has(l.id) && (l.type === "fill" || l.type === "line"),
        );
        setLayers(filtered);
      })
      .catch(() => {
        // Silent: manifest is optional. Map still works without overlays.
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, []);

  if (layers.length === 0) return null;

  return (
    <>
      {layers.map((layer) => {
        const sourceId = `manifest-${layer.id}`;
        if (layer.type === "fill") {
          return (
            <Source
              key={sourceId}
              id={sourceId}
              type="geojson"
              data={layer.geojson_path}
            >
              <Layer
                id={`${sourceId}-fill`}
                type="fill"
                paint={{
                  "fill-color": layer.legend_color,
                  "fill-opacity": layer.opacity,
                }}
              />
              <Layer
                id={`${sourceId}-fill-outline`}
                type="line"
                paint={{
                  "line-color": layer.legend_color,
                  "line-opacity": Math.min(1, layer.opacity + 0.2),
                  "line-width": 0.6,
                }}
              />
            </Source>
          );
        }
        return (
          <Source
            key={sourceId}
            id={sourceId}
            type="geojson"
            data={layer.geojson_path}
          >
            <Layer
              id={`${sourceId}-line`}
              type="line"
              paint={{
                "line-color": layer.legend_color,
                "line-opacity": layer.opacity,
                "line-width": 1.1,
              }}
              layout={{ "line-join": "round", "line-cap": "round" }}
            />
          </Source>
        );
      })}
    </>
  );
}
