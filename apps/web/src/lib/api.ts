import type { OptimizeRequest, OptimizeResponse } from "@/types/optimize";

/** Normalized API origin (no trailing slash). Empty when unset. */
export function apiBase(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  return raw.replace(/\/$/, "");
}

function shouldMock(): boolean {
  if (!apiBase()) return true;
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("mock") === "1";
}

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
