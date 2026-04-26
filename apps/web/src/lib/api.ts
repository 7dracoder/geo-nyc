import type { OptimizeRequest, OptimizeResponse } from "@/types/optimize";
import { safeUpstreamOrigin } from "../../upstreamUrl";

const upstreamApi = safeUpstreamOrigin(process.env.NEXT_PUBLIC_API_BASE_URL);

/**
 * Base URL for API and static layer requests from this app.
 * In the browser we use same-origin `/geo-nyc-proxy/...` (see `next.config.ts` rewrites)
 * so Vercel → ngrok does not hit browser CORS. On the server, the real upstream URL is used.
 */
export function apiBase(): string {
  if (!upstreamApi) return "";
  if (typeof window !== "undefined") return "/geo-nyc-proxy";
  return upstreamApi;
}

function shouldMock(): boolean {
  if (!upstreamApi) return true;
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("mock") === "1";
}

// --- Run API -------------------------------------------------------------

export type RunManifestArtifact = {
  kind: string;
  filename: string;
  url: string;
  bytes: number;
  metadata?: Record<string, unknown>;
};

export type RunManifest = {
  run_id: string;
  status: string;
  mode: string;
  artifacts: RunManifestArtifact[];
  mesh_summary?: Record<string, unknown>;
  field_summary?: Record<string, unknown>;
};

/**
 * Trigger a new run centred on the given WGS-84 coordinates.
 * Returns the full manifest including the mesh artifact URL.
 */
export async function createRun(lng: number, lat: number): Promise<RunManifest> {
  const base = apiBase();
  if (!base) throw new Error("API base URL not configured");

  const res = await fetch(`${base}/api/run`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "1",
    },
    body: JSON.stringify({ center_lng: lng, center_lat: lat }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Run failed (${res.status})`);
  }

  return (await res.json()) as RunManifest;
}

/**
 * Convert a backend-stamped absolute mesh URL to a same-origin proxy path
 * so the browser can fetch it without CORS issues.
 */
export function proxiedMeshUrl(backendUrl: string): string {
  const base = apiBase();
  if (!base || !backendUrl) return backendUrl;
  if (backendUrl.startsWith("/")) return `${base}${backendUrl}`;
  try {
    const u = new URL(backendUrl);
    return `${base}${u.pathname}`;
  } catch {
    return backendUrl;
  }
}

// --- Optimize API --------------------------------------------------------

function mockOptimize(body: OptimizeRequest): OptimizeResponse {
  const mid = (body.params.d_min + body.params.d_max) / 2;
  const bias = body.mode === "tunnel" ? 2 : 0;
  return {
    optimal_d: Math.min(
      body.params.d_max,
      Math.max(body.params.d_min, mid + bias),
    ),
    objective: 1.12 + bias * 0.05,
    constraints_ok: true,
    diagnostics: {
      message:
        "Mock optimizer — set NEXT_PUBLIC_API_BASE_URL and omit ?mock=1 for a live API.",
      mode: body.mode,
    },
  };
}

export async function optimize(body: OptimizeRequest): Promise<OptimizeResponse> {
  if (shouldMock()) {
    await new Promise((r) => setTimeout(r, 80));
    return mockOptimize(body);
  }

  const res = await fetch(`${apiBase()}/api/optimize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Optimize failed (${res.status})`);
  }

  return (await res.json()) as OptimizeResponse;
}
