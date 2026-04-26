"use client";

import { MapPin, Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { geocodeAddress, searchAddressSuggestions, type GeocodeHit } from "@/lib/geocode";
import type { MapPickLocation } from "@/types/map-pick";

type AddressSearchProps = {
  onPick: (lngLat: MapPickLocation) => void;
};

const DEBOUNCE_MS = 380;

export function AddressSearch({ onPick }: AddressSearchProps) {
  const [value, setValue] = useState("");
  const [suggestions, setSuggestions] = useState<GeocodeHit[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = value.trim();
    if (q.length < 3) {
      setSuggestions([]);
      setOpen(false);
      setSuggestLoading(false);
      return;
    }

    const ac = new AbortController();
    const t = window.setTimeout(() => {
      setSuggestLoading(true);
      void searchAddressSuggestions(q, ac.signal)
        .then((hits) => {
          setSuggestions(hits);
          setActiveIdx(0);
          setOpen(hits.length > 0);
        })
        .catch((e: unknown) => {
          if ((e as Error).name === "AbortError") return;
          setSuggestions([]);
          setOpen(false);
        })
        .finally(() => {
          setSuggestLoading(false);
        });
    }, DEBOUNCE_MS);

    return () => {
      window.clearTimeout(t);
      ac.abort();
    };
  }, [value]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const applyHit = useCallback(
    (hit: GeocodeHit) => {
      onPick({ lng: hit.lng, lat: hit.lat });
      setValue(hit.displayName.split(",").slice(0, 2).join(",").trim());
      setOpen(false);
      setSuggestions([]);
      setError(null);
    },
    [onPick],
  );

  const submit = useCallback(async () => {
    setError(null);
    setLoading(true);
    setOpen(false);
    try {
      const hit = await geocodeAddress(value);
      applyHit(hit);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Search failed.");
    } finally {
      setLoading(false);
    }
  }, [value, applyHit]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || suggestions.length === 0) {
      if (e.key === "Enter") {
        e.preventDefault();
        void submit();
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      applyHit(suggestions[activeIdx] ?? suggestions[0]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  return (
    <section className="border-b border-line px-3 py-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <MapPin className="h-4 w-4 shrink-0 text-muted" strokeWidth={1.75} aria-hidden />
        Address
      </h2>
      <form
        className="mt-2 flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (open && suggestions.length > 0) {
            applyHit(suggestions[activeIdx] ?? suggestions[0]);
          } else {
            void submit();
          }
        }}
      >
        <div ref={wrapRef} className="relative">
          <input
            type="text"
            role="combobox"
            aria-expanded={open}
            aria-controls="address-suggest-list"
            aria-activedescendant={
              open && suggestions[activeIdx]
                ? `addr-suggest-${activeIdx}`
                : undefined
            }
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onFocus={() => {
              if (suggestions.length > 0) setOpen(true);
            }}
            onKeyDown={onKeyDown}
            placeholder="Search…"
            autoComplete="off"
            disabled={loading}
            className="w-full rounded-md border border-line bg-white px-2.5 py-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            aria-label="Address or place search"
          />
          {suggestLoading ? (
            <div className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted">
              …
            </div>
          ) : null}
          {open && suggestions.length > 0 ? (
            <ul
              id="address-suggest-list"
              role="listbox"
              className="absolute z-50 mt-1 max-h-52 w-full overflow-y-auto rounded-md border border-line bg-white py-1 shadow-lg"
            >
              {suggestions.map((hit, i) => (
                <li key={`${hit.lng},${hit.lat},${i}`} role="presentation">
                  <button
                    type="button"
                    id={`addr-suggest-${i}`}
                    role="option"
                    aria-selected={i === activeIdx}
                    className={`w-full px-2.5 py-2 text-left text-xs leading-snug hover:bg-stone-100 ${
                      i === activeIdx ? "bg-stone-100" : ""
                    }`}
                    onMouseEnter={() => setActiveIdx(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      applyHit(hit);
                    }}
                  >
                    {hit.displayName}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <button
          type="submit"
          disabled={loading || !value.trim()}
          className="inline-flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 hover:opacity-95"
        >
          <Search className="h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
          {loading ? "…" : "Go"}
        </button>
      </form>
      {error ? <p className="mt-2 text-xs text-red-800">{error}</p> : null}
      <p className="mt-2 text-[10px] text-muted">
        ©{" "}
        <a
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent hover:underline"
        >
          OpenStreetMap
        </a>
      </p>
    </section>
  );
}
