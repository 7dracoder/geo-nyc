import { apiBase } from "@/lib/api";

/** Known-good glTF (no Draco) for preview when no local asset exists. */
export const KHRONOS_DUCK_GLTF =
  "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb";

const LOCAL_GLB = "/exports/sample.glb";

function trimEnv(url: string | undefined): string | null {
  const t = url?.trim();
  return t && t.length > 0 ? t : null;
}

async function headOk(url: string): Promise<boolean> {
  try {
    const r = await fetch(url, { method: "HEAD", cache: "no-store" });
    return r.ok;
  } catch {
    return false;
  }
}

/**
 * Pick a GLB URL: env override → backend static (same-origin proxy) → local public
 * → Khronos Duck (always available for a real mesh when nothing else exists).
 */
export async function resolveGltfUrl(): Promise<string> {
  const fromEnv = trimEnv(process.env.NEXT_PUBLIC_GLTF_URL);
  if (fromEnv) return fromEnv;

  const base = typeof window !== "undefined" ? apiBase() : "";
  if (base) {
    const proxied = `${base}/static/exports/sample.glb`;
    if (await headOk(proxied)) return proxied;
  }

  if (await headOk(LOCAL_GLB)) return LOCAL_GLB;

  return KHRONOS_DUCK_GLTF;
}
