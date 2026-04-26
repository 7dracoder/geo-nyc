"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchDisplayableManifestLayers, type ManifestLayer } from "@/lib/manifestLayers";
import type { MapPickLocation } from "@/types/map-pick";
import { AddressSearch } from "./AddressSearch";
import { LayerPanel } from "./LayerPanel";
import { MapView, type MapViewHandle } from "./MapView";
import { WhatIfPanel } from "./WhatIfPanel";

const SubsurfaceViewer = dynamic(
  () =>
    import("./SubsurfaceViewer").then((m) => ({ default: m.SubsurfaceViewer })),
  {
    ssr: false,
    loading: () => (
      <div
        className="pointer-events-none absolute inset-x-3 bottom-12 z-[15] flex h-[15rem] items-center justify-center rounded-lg border border-line bg-panel/90 text-xs text-muted shadow-md backdrop-blur-sm sm:inset-x-4 sm:bottom-10"
        aria-hidden
      >
        …
      </div>
    ),
  },
);

export function AppShell() {
  const [picked, setPicked] = useState<MapPickLocation | null>(null);
  const [manifestLayers, setManifestLayers] = useState<ManifestLayer[]>([]);
  const [layerEnabled, setLayerEnabled] = useState<Record<string, boolean>>({});
  const [fitBusy, setFitBusy] = useState(false);
  const mapRef = useRef<MapViewHandle>(null);

  useEffect(() => {
    const ac = new AbortController();
    fetchDisplayableManifestLayers(ac.signal)
      .then((list) => {
        setManifestLayers(list);
        setLayerEnabled((prev) => {
          const next = { ...prev };
          for (const l of list) {
            if (next[l.id] === undefined) next[l.id] = true;
          }
          return next;
        });
      })
      .catch(() => setManifestLayers([]));
    return () => ac.abort();
  }, []);

  const onPick = useCallback((lngLat: MapPickLocation) => {
    setPicked(lngLat);
  }, []);

  const clearPick = useCallback(() => {
    setPicked(null);
  }, []);

  const onLayerToggle = useCallback((id: string, on: boolean) => {
    setLayerEnabled((e) => ({ ...e, [id]: on }));
  }, []);

  const onShowAllLayers = useCallback(() => {
    setLayerEnabled((e) => {
      const next = { ...e };
      for (const l of manifestLayers) next[l.id] = true;
      return next;
    });
  }, [manifestLayers]);

  const onHideAllLayers = useCallback(() => {
    setLayerEnabled((e) => {
      const next = { ...e };
      for (const l of manifestLayers) next[l.id] = false;
      return next;
    });
  }, [manifestLayers]);

  const onFitLayers = useCallback(async () => {
    setFitBusy(true);
    try {
      await mapRef.current?.fitVisibleOverlays();
    } finally {
      setFitBusy(false);
    }
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-panel px-4 py-3">
        <h1 className="font-serif-display text-xl font-semibold tracking-tight text-ink md:text-2xl">
          geoNYC
        </h1>
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="w-full shrink-0 border-line bg-panel md:w-[17.75rem] md:border-r">
          <AddressSearch onPick={onPick} />
          <WhatIfPanel />
          <LayerPanel
            layers={manifestLayers}
            enabled={layerEnabled}
            onToggle={onLayerToggle}
            onShowAll={onShowAllLayers}
            onHideAll={onHideAllLayers}
            onFitVisible={onFitLayers}
            fitBusy={fitBusy}
          />
        </aside>

        <main className="flex min-h-0 flex-1 flex-col">
          <div className="relative min-h-[min(48vh,420px)] flex-1 bg-paper md:p-4">
            <MapView
              ref={mapRef}
              pick={picked}
              onPick={onPick}
              manifestLayers={manifestLayers}
              layerEnabled={layerEnabled}
            />
            <SubsurfaceViewer pick={picked} onClearPick={clearPick} />
          </div>
        </main>
      </div>
    </div>
  );
}
