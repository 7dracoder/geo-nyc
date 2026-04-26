"use client";

import { useEffect, useState } from "react";
import { Loader2, SlidersHorizontal } from "lucide-react";
import { optimize } from "@/lib/api";
import type { OptimizeMode, OptimizeResponse } from "@/types/optimize";

const DEBOUNCE_MS = 280;

export function WhatIfPanel() {
  const [mode, setMode] = useState<OptimizeMode>("geothermal");
  const [dMin, setDMin] = useState(8);
  const [dMax, setDMax] = useState(55);
  const [lateral, setLateral] = useState(0);
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const lo = Math.min(dMin, dMax);
    const hi = Math.max(dMin, dMax);
    const handle = window.setTimeout(() => {
      setPending(true);
      setError(null);
      optimize({
        mode,
        params: {
          d_min: lo,
          d_max: hi,
          lateral_m: mode === "tunnel" ? lateral : undefined,
        },
      })
        .then(setResult)
        .catch((e: unknown) => {
          setError(e instanceof Error ? e.message : "Request failed");
          setResult(null);
        })
        .finally(() => setPending(false));
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [mode, dMin, dMax, lateral]);

  return (
    <section className="px-3 py-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <SlidersHorizontal
          className="h-4 w-4 shrink-0 text-muted"
          strokeWidth={1.75}
          aria-hidden
        />
        What-if
      </h2>

      <div className="mt-2 flex gap-1 rounded-[6px] border border-line bg-paper p-0.5">
        {(
          [
            ["geothermal", "Geothermal"],
            ["tunnel", "Tunnel"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setMode(value)}
            className={`flex-1 rounded-[5px] px-2 py-1.5 text-xs font-medium transition-colors ${
              mode === value
                ? "bg-panel text-ink shadow-sm"
                : "text-muted hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="mt-3 space-y-3">
        <div>
          <label className="text-xs font-medium text-muted">Depth min (m)</label>
          <input
            type="number"
            value={dMin}
            onChange={(e) => setDMin(Number(e.target.value))}
            className="mt-0.5 w-full rounded-[6px] border border-line bg-panel px-2 py-1.5 text-sm text-ink"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-muted">Depth max (m)</label>
          <input
            type="number"
            value={dMax}
            onChange={(e) => setDMax(Number(e.target.value))}
            className="mt-0.5 w-full rounded-[6px] border border-line bg-panel px-2 py-1.5 text-sm text-ink"
          />
        </div>
        {mode === "tunnel" ? (
          <div>
            <label className="text-xs font-medium text-muted">Lateral offset (m)</label>
            <input
              type="number"
              value={lateral}
              onChange={(e) => setLateral(Number(e.target.value))}
              className="mt-0.5 w-full rounded-[6px] border border-line bg-panel px-2 py-1.5 text-sm text-ink"
            />
          </div>
        ) : null}
      </div>

      <div className="mt-4 rounded-[6px] border border-line bg-paper px-2.5 py-2">
        <div className="flex items-center gap-2 text-xs font-medium text-muted">
          Result
          {pending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" aria-label="Loading" />
          ) : null}
        </div>
        {error ? (
          <p className="mt-1 text-sm text-red-800">{error}</p>
        ) : result ? (
          <dl className="mt-2 space-y-1 text-sm text-ink">
            <div className="flex justify-between gap-2">
              <dt className="text-muted">Optimal depth</dt>
              <dd className="font-medium tabular-nums">{result.optimal_d.toFixed(1)} m</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted">Objective</dt>
              <dd className="tabular-nums">{result.objective.toFixed(3)}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted">Constraints</dt>
              <dd>{result.constraints_ok ? "Satisfied" : "Violated"}</dd>
            </div>
          </dl>
        ) : null}
      </div>
    </section>
  );
}
