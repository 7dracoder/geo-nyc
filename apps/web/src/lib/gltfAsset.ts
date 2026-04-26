import { apiBase } from "@/lib/api";

/** Known-good glTF (no Draco) when no pipeline mesh is available. */
const KHRONOS_DUCK_GLTF =
  "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb";

const LOCAL_GLB = "/exports/sample.glb";

export type RunSurfaceMeta = {
  surface_id: string;
  name: string;
  rock_type?: string;
  color_hex?: string;
};

type ArtifactJson = {
  kind?: string;
  filename?: string;
  url?: string;
  metadata?: Record<string, unknown>;
};

type RunManifestJson = {
  run_id?: string;
  status?: string;
  created_at?: string;
  mode?: string;
  artifacts?: ArtifactJson[];
};

type RunListJson = {
  items?: RunManifestJson[];
};

export type RunResolution = {
  url: string;
  runId?: string;
  mode?: string;
  surfaces: RunSurfaceMeta[];
  source: string;
};

/**
 * Higher = more interesting. Picks operator-supplied / PDF-driven runs
 * ahead of fixture runs so the dock shows real data once the user has
 * built one from the dock or the operator has run `make seed-runs`.
 *
 * When a build kicked off from the dock pins `currentRunId` directly,
 * this priority list is bypassed entirely (see {@link resolveGltfUrl}).
 */
function priorityForMode(mode: string | undefined): number {
  const m = (mode ?? "").toLowerCase();
  if (m.includes("inline_dsl")) return 5;
  if (m.includes("document_llm_dsl")) return 4;
  if (m.includes("document_llm")) return 3;
  if (m.includes("document_chunks")) return 2;
  if (m.includes("document")) return 2;
  return 1;
}

function trimEnv(url: string | undefined): string | null {
  const t = url?.trim();
  return t && t.length > 0 ? t : null;
}

/** Turn backend absolute `PUBLIC_BASE_URL/static/...` into same-origin proxy path. */
function proxiedStaticFetchUrl(base: string, backendUrl: string): string {
  if (!backendUrl) return "";
  if (backendUrl.startsWith("/")) {
    return `${base}${backendUrl}`;
  }
  try {
    const u = new URL(backendUrl);
    return `${base}${u.pathname}`;
  } catch {
    return backendUrl;
  }
}

/**
 * Probe a URL to confirm it returns binary/glTF content, not an HTML
 * interstitial (free ngrok) or a 404 page that still 200s.
 *
 * Some stacks reject HEAD; fall back to a tiny ranged GET. Any
 * `text/html` response is treated as failure so the resolver moves on
 * to the next candidate (otherwise `useGLTF` later parses HTML and
 * silently falls back to the Khronos duck).
 */
async function resourceOk(url: string): Promise<boolean> {
  const looksHtml = (ct: string | null) =>
    !!ct && /text\/html|application\/xhtml\+xml/i.test(ct);
  try {
    const head = await fetch(url, {
      method: "HEAD",
      cache: "no-store",
      headers: { "ngrok-skip-browser-warning": "1" },
    });
    if (head.ok && !looksHtml(head.headers.get("content-type"))) return true;
    if (head.ok && looksHtml(head.headers.get("content-type"))) return false;
    if (head.status !== 405 && head.status !== 501) return false;
  } catch {
    // fall through to GET
  }
  try {
    const get = await fetch(url, {
      method: "GET",
      cache: "no-store",
      headers: {
        Range: "bytes=0-0",
        "ngrok-skip-browser-warning": "1",
      },
    });
    if (!(get.ok || get.status === 206)) return false;
    return !looksHtml(get.headers.get("content-type"));
  } catch {
    return false;
  }
}

async function fetchJsonNoHtml<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, {
      cache: "no-store",
      headers: { "ngrok-skip-browser-warning": "1" },
    });
    if (!r.ok) return null;
    const ct = r.headers.get("content-type") ?? "";
    if (/text\/html|application\/xhtml\+xml/i.test(ct)) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

function pickMeshArtifact(manifest: RunManifestJson): ArtifactJson | undefined {
  return (manifest.artifacts ?? []).find((a) => {
    if (a.kind !== "mesh") return false;
    const fn = a.filename?.toLowerCase() ?? "";
    const u = a.url?.toLowerCase() ?? "";
    return fn.endsWith(".glb") || u.includes(".glb");
  });
}

function surfacesFromManifest(manifest: RunManifestJson): RunSurfaceMeta[] {
  const mesh = pickMeshArtifact(manifest);
  const raw = mesh?.metadata?.surfaces;
  if (!Array.isArray(raw)) return [];
  const out: RunSurfaceMeta[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const id = typeof o.surface_id === "string" ? o.surface_id : null;
    const name = typeof o.name === "string" ? o.name : id;
    if (!id || !name) continue;
    out.push({
      surface_id: id,
      name,
      rock_type: typeof o.rock_type === "string" ? o.rock_type : undefined,
      color_hex: typeof o.color_hex === "string" ? o.color_hex : undefined,
    });
  }
  return out;
}

async function meshUrlFromManifest(base: string, manifest: RunManifestJson): Promise<string | null> {
  const mesh = pickMeshArtifact(manifest);
  const runId = manifest.run_id;
  if (mesh?.url) {
    const proxied = proxiedStaticFetchUrl(base, mesh.url);
    if (await resourceOk(proxied)) return proxied;
  }
  if (runId) {
    const name = mesh?.filename ?? "model.glb";
    const candidate = `${base}/static/exports/${encodeURIComponent(runId)}/${encodeURIComponent(name)}`;
    if (await resourceOk(candidate)) return candidate;
  }
  return null;
}

async function meshUrlFromConfiguredRunId(base: string, runId: string): Promise<string | null> {
  const manifest = await fetchJsonNoHtml<RunManifestJson>(
    `${base}/api/run/${encodeURIComponent(runId)}`,
  );
  if (!manifest) return null;
  return meshUrlFromManifest(base, manifest);
}

/**
 * Fetch a *specific* run's manifest + GLB. Used right after the dock
 * builds a new run from operator DSL / file upload — we want to display
 * exactly that run, not whatever wins the latest-run priority sort.
 */
export async function resolveRunById(runId: string): Promise<RunResolution | null> {
  const base = typeof window !== "undefined" ? apiBase() : "";
  if (!base) return null;
  const manifest = await fetchJsonNoHtml<RunManifestJson>(
    `${base}/api/run/${encodeURIComponent(runId)}`,
  );
  if (!manifest) return null;
  const url = await meshUrlFromManifest(base, manifest);
  if (!url) return null;
  return {
    url,
    runId: manifest.run_id,
    mode: manifest.mode,
    surfaces: surfacesFromManifest(manifest),
    source: `pinned run (${manifest.mode ?? "?"}, ${manifest.run_id ?? "?"})`,
  };
}

type LatestPick = { url: string; manifest: RunManifestJson };

function manifestToResolution(
  url: string,
  manifest: RunManifestJson,
  source: string,
): RunResolution {
  return {
    url,
    runId: manifest.run_id,
    mode: manifest.mode,
    surfaces: surfacesFromManifest(manifest),
    source,
  };
}

async function meshUrlFromLatestRun(base: string): Promise<LatestPick | null> {
  const body = await fetchJsonNoHtml<RunListJson>(`${base}/api/runs?limit=40`);
  if (!body) return null;
  const items = body.items ?? [];
  // Sort by (succeeded > others) then (PDF-driven mode > fixture) then
  // (newest first). The mode tier is the new bit: once `make seed-runs`
  // has produced a `document_llm_dsl` run, the dock uses it even if a
  // fresher fixture run exists.
  const sorted = [...items].sort((a, b) => {
    const sa = a.status === "succeeded" ? 1 : 0;
    const sb = b.status === "succeeded" ? 1 : 0;
    if (sa !== sb) return sb - sa;
    const pa = priorityForMode(a.mode);
    const pb = priorityForMode(b.mode);
    if (pa !== pb) return pb - pa;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });
  for (const m of sorted) {
    const url = await meshUrlFromManifest(base, m);
    if (url) return { url, manifest: m };
  }
  return null;
}

/**
 * Pick a GLB for the 3D dock:
 *
 * 1. `NEXT_PUBLIC_GLTF_URL` — explicit URL
 * 2. `NEXT_PUBLIC_MESH_RUN_ID` + API — that run’s mesh (`model.glb` from the DSL pipeline; engine may be GemPy, RBF, or synthetic depending on backend config)
 * 3. Latest run from `GET /api/runs` that still has a mesh on disk (same-origin proxy)
 * 4. `static/exports/sample.glb` then `public/exports/sample.glb` if present
 * 5. Khronos Duck fallback
 */
export async function resolveGltfUrl(): Promise<string> {
  const log = (where: string, url: string) => {
    if (typeof window !== "undefined") {
      console.info(`[geo-nyc] subsurface GLB resolved via ${where}:`, url);
    }
  };

  const fromEnv = trimEnv(process.env.NEXT_PUBLIC_GLTF_URL);
  if (fromEnv) {
    log("NEXT_PUBLIC_GLTF_URL", fromEnv);
    return fromEnv;
  }

  const base = typeof window !== "undefined" ? apiBase() : "";
  if (base) {
    const runId = trimEnv(process.env.NEXT_PUBLIC_MESH_RUN_ID);
    if (runId) {
      const pinned = await meshUrlFromConfiguredRunId(base, runId);
      if (pinned) {
        log(`NEXT_PUBLIC_MESH_RUN_ID=${runId}`, pinned);
        return pinned;
      }
    }

    const latest = await meshUrlFromLatestRun(base);
    if (latest) {
      const m = latest.manifest;
      const tag = m.mode
        ? `latest run (${m.mode}, ${m.run_id ?? "?"})`
        : `latest run (${m.run_id ?? "?"})`;
      log(tag, latest.url);
      return latest.url;
    }

    const legacySample = `${base}/static/exports/sample.glb`;
    if (await resourceOk(legacySample)) {
      log("legacy sample.glb", legacySample);
      return legacySample;
    }
  } else if (typeof window !== "undefined") {
    console.warn(
      "[geo-nyc] NEXT_PUBLIC_API_BASE_URL is not set or invalid; the dock cannot reach a run mesh.",
    );
  }

  if (await resourceOk(LOCAL_GLB)) {
    log("public/exports/sample.glb", LOCAL_GLB);
    return LOCAL_GLB;
  }

  log("Khronos Duck (last-resort fallback)", KHRONOS_DUCK_GLTF);
  return KHRONOS_DUCK_GLTF;
}
