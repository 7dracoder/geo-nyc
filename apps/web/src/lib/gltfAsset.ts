import { apiBase } from "@/lib/api";

/** Known-good glTF (no Draco) when no pipeline mesh is available. */
export const KHRONOS_DUCK_GLTF =
  "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb";

const LOCAL_GLB = "/exports/sample.glb";

type ArtifactJson = {
  kind?: string;
  filename?: string;
  url?: string;
};

type RunManifestJson = {
  run_id?: string;
  status?: string;
  created_at?: string;
  artifacts?: ArtifactJson[];
};

type RunListJson = {
  items?: RunManifestJson[];
};

function trimEnv(url: string | undefined): string | null {
  const t = url?.trim();
  return t && t.length > 0 ? t : null;
}

/** Turn backend absolute `PUBLIC_BASE_URL/static/...` into same-origin proxy path. */
export function proxiedStaticFetchUrl(base: string, backendUrl: string): string {
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

/** Some stacks (or dev rewrites) reject HEAD; fall back to a tiny ranged GET. */
async function resourceOk(url: string): Promise<boolean> {
  try {
    let r = await fetch(url, { method: "HEAD", cache: "no-store" });
    if (r.ok) return true;
    if (r.status === 405 || r.status === 501) {
      r = await fetch(url, {
        method: "GET",
        cache: "no-store",
        headers: { Range: "bytes=0-0" },
      });
      return r.ok || r.status === 206;
    }
    return false;
  } catch {
    return false;
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
  const r = await fetch(`${base}/api/run/${encodeURIComponent(runId)}`, {
    cache: "no-store",
  });
  if (!r.ok) return null;
  const manifest = (await r.json()) as RunManifestJson;
  return meshUrlFromManifest(base, manifest);
}

async function meshUrlFromLatestRun(base: string): Promise<string | null> {
  const r = await fetch(`${base}/api/runs?limit=40`, { cache: "no-store" });
  if (!r.ok) return null;
  const body = (await r.json()) as RunListJson;
  const items = body.items ?? [];
  const sorted = [...items].sort((a, b) => {
    const ta = a.created_at ?? "";
    const tb = b.created_at ?? "";
    return tb.localeCompare(ta);
  });
  const preferSucceeded = sorted.filter((m) => m.status === "succeeded");
  const rest = sorted.filter((m) => m.status !== "succeeded");
  for (const m of [...preferSucceeded, ...rest]) {
    const url = await meshUrlFromManifest(base, m);
    if (url) return url;
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
  const fromEnv = trimEnv(process.env.NEXT_PUBLIC_GLTF_URL);
  if (fromEnv) return fromEnv;

  const base = typeof window !== "undefined" ? apiBase() : "";
  if (base) {
    const runId = trimEnv(process.env.NEXT_PUBLIC_MESH_RUN_ID);
    if (runId) {
      const pinned = await meshUrlFromConfiguredRunId(base, runId);
      if (pinned) return pinned;
    }

    const latest = await meshUrlFromLatestRun(base);
    if (latest) return latest;

    const legacySample = `${base}/static/exports/sample.glb`;
    if (await resourceOk(legacySample)) return legacySample;
  }

  if (await resourceOk(LOCAL_GLB)) return LOCAL_GLB;

  return KHRONOS_DUCK_GLTF;
}
