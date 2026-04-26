"use client";

import dynamic from "next/dynamic";
import { useCallback, useState } from "react";
import type { MapPickLocation } from "@/types/map-pick";
import { AddressSearch } from "./AddressSearch";
import { MapView } from "./MapView";
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

  const onPick = useCallback((lngLat: MapPickLocation) => {
    setPicked(lngLat);
  }, []);

  const clearPick = useCallback(() => {
    setPicked(null);
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
        </aside>

        <main className="flex min-h-0 flex-1 flex-col">
          <div className="relative min-h-[min(48vh,420px)] flex-1 bg-paper md:p-4">
            <MapView pick={picked} onPick={onPick} />
            <SubsurfaceViewer pick={picked} onClearPick={clearPick} />
          </div>
        </main>
      </div>
    </div>
  );
}
