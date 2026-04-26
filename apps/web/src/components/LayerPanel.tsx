"use client";

import { useCallback } from "react";
import { Layers, MapPin } from "lucide-react";
import type { ManifestLayer } from "@/lib/manifestLayers";

type LayerPanelProps = {
  layers: ManifestLayer[];
  enabled: Record<string, boolean>;
  onToggle: (id: string, on: boolean) => void;
  onShowAll: () => void;
  onHideAll: () => void;
  onFitVisible: () => void;
  fitBusy?: boolean;
};

export function LayerPanel({
  layers,
  enabled,
  onToggle,
  onShowAll,
  onHideAll,
  onFitVisible,
  fitBusy,
}: LayerPanelProps) {
  const visibleCount = layers.filter((l) => enabled[l.id] !== false).length;

  const onFitClick = useCallback(() => {
    if (visibleCount === 0 || fitBusy) return;
    onFitVisible();
  }, [fitBusy, onFitVisible, visibleCount]);

  return (
    <section className="border-t border-line px-3 py-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <Layers className="h-4 w-4 shrink-0 text-muted" strokeWidth={1.75} aria-hidden />
        Layers
      </h2>
      <p className="mt-1 text-[11px] leading-snug text-muted">
        Toggle data overlays. Fit zooms the map to the union of visible layer bounds.
      </p>

      {layers.length === 0 ? (
        <p className="mt-3 text-xs text-muted">No manifest overlays (add manifest + GeoJSON under public or API).</p>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={onShowAll}
              className="rounded-[5px] border border-line bg-paper px-2 py-1 text-[11px] font-medium text-ink hover:bg-panel"
            >
              Show all
            </button>
            <button
              type="button"
              onClick={onHideAll}
              className="rounded-[5px] border border-line bg-paper px-2 py-1 text-[11px] font-medium text-muted hover:text-ink"
            >
              Hide all
            </button>
            <button
              type="button"
              onClick={onFitClick}
              disabled={visibleCount === 0 || fitBusy}
              className="inline-flex items-center gap-1 rounded-[5px] border border-line bg-paper px-2 py-1 text-[11px] font-medium text-ink hover:bg-panel disabled:cursor-not-allowed disabled:opacity-45"
            >
              <MapPin className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden />
              {fitBusy ? "Fitting…" : "Fit to map"}
            </button>
          </div>

          <ul className="mt-3 max-h-[11rem] space-y-2 overflow-y-auto pr-0.5">
            {layers.map((layer) => {
              const on = enabled[layer.id] !== false;
              return (
                <li key={layer.id} className="flex items-start gap-2">
                  <input
                    id={`layer-${layer.id}`}
                    type="checkbox"
                    checked={on}
                    onChange={(e) => onToggle(layer.id, e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-line text-accent focus:ring-accent"
                  />
                  <label
                    htmlFor={`layer-${layer.id}`}
                    className="min-w-0 flex-1 cursor-pointer text-xs leading-snug text-ink"
                  >
                    <span className="font-medium">{layer.title}</span>
                    <span className="ml-1.5 font-mono text-[10px] text-muted">{layer.type}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
