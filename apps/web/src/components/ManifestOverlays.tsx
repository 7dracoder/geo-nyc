"use client";

import { Layer, Source } from "react-map-gl/maplibre";
import type { ManifestLayer } from "@/lib/manifestLayers";

type ManifestOverlaysProps = {
  layers: ManifestLayer[];
  /** When false or missing for an id, that overlay is hidden. */
  enabled: Record<string, boolean>;
};

export function ManifestOverlays({ layers, enabled }: ManifestOverlaysProps) {
  if (layers.length === 0) return null;

  return (
    <>
      {layers.map((layer) => {
        if (enabled[layer.id] === false) return null;
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
