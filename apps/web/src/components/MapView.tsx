"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import type { MapLayerMouseEvent } from "maplibre-gl";
import { useCallback, useEffect, useRef } from "react";
import {
  type BoroughFeatureCollection,
  isLngLatInsideNycFiveBoroughs,
} from "@/lib/nyc-borough-hit";
import { hidePositronPlaceAndWaterLabels } from "@/lib/positron-label-cleanup";
import Map, {
  Layer,
  MapRef,
  Marker,
  NavigationControl,
  Source,
} from "react-map-gl/maplibre";
import type { MapPickLocation } from "@/types/map-pick";
import { ManifestOverlays } from "./ManifestOverlays";

/**
 * Fixed basemap (Carto Positron). Borough mask, labels, and boundary are always on.
 */
const MAP_STYLE =
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

/** Tight NYC framing for initial fit. */
const NYC_FIVE_BORO_BOUNDS: [[number, number], [number, number]] = [
  [-74.26, 40.477],
  [-73.69, 40.92],
];

/**
 * Pan limits: same outer rectangle as `nyc_outside_mask.geojson` (see
 * `scripts/build-nyc-outside-mask.mjs`).
 */
const NYC_PAN_MAX_BOUNDS: [[number, number], [number, number]] = [
  [-74.56, 40.21],
  [-73.14, 41.19],
];

/** Hides basemap outside official borough polygons (holes). Regenerate: `npm run layers:mask`. */
const NYC_OUTSIDE_MASK = "/layers/nyc_outside_mask.geojson";

/** Approximate label anchors; must match glyphs in Carto Positron style */
const BOROUGH_LABELS = "/layers/borough_labels.geojson";

const BOROUGH_GEOMETRY = "/layers/boroughs_nyc.geojson";

/** Fallbacks match Carto Positron `style.json` glyph stacks */
const POSITRON_TEXT_FONT: string[] = [
  "Montserrat Medium",
  "Open Sans Bold",
  "Noto Sans Regular",
  "HanWangHeiLight Regular",
  "NanumBarunGothic Regular",
];

const INITIAL_VIEW = {
  longitude: (NYC_FIVE_BORO_BOUNDS[0][0] + NYC_FIVE_BORO_BOUNDS[1][0]) / 2,
  latitude: (NYC_FIVE_BORO_BOUNDS[0][1] + NYC_FIVE_BORO_BOUNDS[1][1]) / 2,
  zoom: 10,
};

/** Outside five boroughs; keep in sync with `--map-outside` in globals.css */
const MASK_FILL_COLOR = "#dde3ee";

/** Permanent borough outline (NYC Open Data, simplified GeoJSON). */
const BOROUGH_LINE_COLOR = "#57534e";

type MapViewProps = {
  pick?: MapPickLocation | null;
  onPick?: (lngLat: MapPickLocation) => void;
};

export function MapView({ pick, onPick }: MapViewProps) {
  const mapRef = useRef<MapRef>(null);
  const boroughFcRef = useRef<BoroughFeatureCollection | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(BOROUGH_GEOMETRY)
      .then((r) => r.json())
      .then((data: BoroughFeatureCollection) => {
        if (!cancelled) boroughFcRef.current = data;
      })
      .catch(() => {
        /* pick guard stays disabled until geometry loads */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onMapClick = useCallback(
    (e: MapLayerMouseEvent) => {
      if (!onPick) return;
      const fc = boroughFcRef.current;
      if (!fc) return;
      const lng = e.lngLat.lng;
      const lat = e.lngLat.lat;
      if (!isLngLatInsideNycFiveBoroughs(lng, lat, fc)) return;
      onPick({ lng, lat });
    },
    [onPick],
  );

  const onLoad = useCallback(() => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    const applyLabelCleanup = () => hidePositronPlaceAndWaterLabels(map);
    applyLabelCleanup();
    map.once("idle", applyLabelCleanup);
    map.fitBounds(NYC_FIVE_BORO_BOUNDS, {
      padding: { top: 72, bottom: 72, left: 72, right: 72 },
      duration: 0,
      maxZoom: 11.15,
    });
  }, []);

  useEffect(() => {
    if (!pick) return;
    const map = mapRef.current?.getMap();
    if (!map) return;
    const run = () => {
      const z = map.getZoom();
      map.flyTo({
        center: [pick.lng, pick.lat],
        zoom: Math.min(16, Math.max(z, 13)),
        duration: 900,
      });
    };
    if (map.isStyleLoaded()) {
      run();
    } else {
      map.once("load", run);
    }
  }, [pick?.lng, pick?.lat]);

  return (
    <div className="map-shell relative isolate h-full min-h-[280px] w-full rounded-none border-0 bg-[var(--map-outside)] md:rounded-2xl md:border md:border-line/70 md:bg-panel">
      <Map
        ref={mapRef}
        initialViewState={INITIAL_VIEW}
        mapStyle={MAP_STYLE}
        attributionControl={{ compact: false }}
        maxBounds={NYC_PAN_MAX_BOUNDS}
        minZoom={8}
        maxZoom={19}
        scrollZoom
        dragPan
        dragRotate={false}
        touchPitch={false}
        doubleClickZoom
        touchZoomRotate
        keyboard
        reuseMaps
        renderWorldCopies={false}
        onLoad={onLoad}
        onClick={onPick ? onMapClick : undefined}
        style={{ width: "100%", height: "100%" }}
      >
        <NavigationControl position="top-right" showCompass={false} />
        <Source id="nyc-outside-mask" type="geojson" data={NYC_OUTSIDE_MASK}>
          <Layer
            id="nyc-outside-mask-fill"
            type="fill"
            paint={{
              "fill-color": MASK_FILL_COLOR,
              "fill-opacity": 1,
            }}
          />
        </Source>
        {/* Part 3 (data layer) overlays from /layers/manifest.json. */}
        <ManifestOverlays />
        <Source id="borough-labels" type="geojson" data={BOROUGH_LABELS}>
          <Layer
            id="borough-labels-symbol"
            type="symbol"
            layout={{
              "text-field": ["get", "name"],
              "text-font": POSITRON_TEXT_FONT,
              "text-size": [
                "interpolate",
                ["linear"],
                ["zoom"],
                7,
                11,
                9,
                14,
                11,
                16,
              ],
              "text-anchor": "center",
              "text-allow-overlap": true,
              "text-ignore-placement": true,
            }}
            paint={{
              "text-color": "#44403c",
              "text-halo-color": "#fffef9",
              "text-halo-width": 1.35,
              "text-halo-blur": 0.35,
              "text-opacity": [
                "interpolate",
                ["linear"],
                ["zoom"],
                7.8,
                0,
                8.25,
                1,
                10.75,
                1,
                12,
                0,
                14,
                0,
              ],
            }}
          />
        </Source>
        <Source id="borough-boundary" type="geojson" data={BOROUGH_GEOMETRY}>
          <Layer
            id="borough-boundary-line"
            type="line"
            paint={{
              "line-color": BOROUGH_LINE_COLOR,
              "line-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.72, 14, 0.92],
              "line-width": [
                "interpolate",
                ["linear"],
                ["zoom"],
                8,
                0.85,
                11,
                1.35,
                14,
                2.1,
                18,
                3.2,
              ],
              "line-blur": 0.4,
            }}
            layout={{
              "line-join": "round",
              "line-cap": "round",
            }}
          />
        </Source>
        {pick ? (
          <Marker
            longitude={pick.lng}
            latitude={pick.lat}
            anchor="center"
            onClick={(e) => {
              e.originalEvent.stopPropagation();
            }}
          >
            <div
              className="h-3.5 w-3.5 rounded-full border-2 border-white bg-accent shadow-[0_2px_12px_rgba(30,58,95,0.45)] ring-2 ring-accent/25"
              aria-hidden
            />
          </Marker>
        ) : null}
      </Map>
    </div>
  );
}
