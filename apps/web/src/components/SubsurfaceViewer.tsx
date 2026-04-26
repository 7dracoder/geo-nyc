"use client";

import { Environment, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import {
  Component,
  type ErrorInfo,
  type ReactNode,
  Suspense,
  useMemo,
  useState,
  useLayoutEffect,
} from "react";
import * as THREE from "three";
import { Box, X } from "lucide-react";
import { resolveGltfUrl } from "@/lib/gltfAsset";
import type { MapPickLocation } from "@/types/map-pick";

const PALETTE = [
  "#f59e0b",
  "#84cc16",
  "#22c55e",
  "#06b6d4",
  "#3b82f6",
  "#8b5cf6",
  "#ec4899",
  "#ef4444",
];

function PlaceholderBlock() {
  return (
    <mesh>
      <boxGeometry args={[1.15, 0.75, 1.15]} />
      <meshStandardMaterial color="#78716c" roughness={0.45} metalness={0.08} />
    </mesh>
  );
}

/**
 * Defensive cleanup of GLBs coming out of the geo-nyc mesh exporter:
 *   - Compute vertex normals when missing (otherwise PBR materials render black).
 *   - Force `DoubleSide` so inconsistent winding doesn't hide layers.
 *   - Clamp metalness/roughness to sane values; brighten near-black baseColor so
 *     a layer that was emitted with `0x000000` is still visible.
 *   - Assign a per-mesh fallback color from PALETTE when material is undefined.
 *   - Drop shadow casting/receiving — the dock's shadow budget is the main
 *     reason WebGL contexts get lost on Vercel iframes alongside MapLibre.
 */
function GlbModel({ url }: { url: string }) {
  const gltf = useGLTF(url);
  const root = useMemo(() => {
    const g = gltf.scene.clone(true);
    let meshIndex = 0;
    g.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      o.castShadow = false;
      o.receiveShadow = false;
      const geom = o.geometry as THREE.BufferGeometry | undefined;
      if (geom && !geom.attributes.normal) {
        geom.computeVertexNormals();
      }
      const fallbackColor = PALETTE[meshIndex % PALETTE.length];
      meshIndex += 1;
      const ensureMaterial = (mat: THREE.Material | null | undefined): THREE.Material => {
        if (!mat) {
          return new THREE.MeshStandardMaterial({
            color: fallbackColor,
            roughness: 0.7,
            metalness: 0.05,
            side: THREE.DoubleSide,
            flatShading: false,
          });
        }
        mat.side = THREE.DoubleSide;
        if (mat instanceof THREE.MeshStandardMaterial) {
          mat.toneMapped = true;
          mat.metalness = Math.min(mat.metalness ?? 0.0, 0.25);
          mat.roughness = Math.max(mat.roughness ?? 0.5, 0.55);
          if (mat.color && mat.color.r + mat.color.g + mat.color.b < 0.15) {
            mat.color.set(fallbackColor);
          }
        }
        if ("color" in mat && mat.color instanceof THREE.Color) {
          if (mat.color.r + mat.color.g + mat.color.b < 0.15) {
            mat.color.set(fallbackColor);
          }
        }
        mat.needsUpdate = true;
        return mat;
      };
      if (Array.isArray(o.material)) {
        o.material = o.material.map((m) => ensureMaterial(m));
      } else {
        o.material = ensureMaterial(o.material as THREE.Material | null | undefined);
      }
    });

    const box = new THREE.Box3().setFromObject(g);
    if (!box.isEmpty()) {
      const size = box.getSize(new THREE.Vector3());
      const max = Math.max(size.x, size.y, size.z, 1e-6);
      g.scale.setScalar(1.85 / max);
      box.setFromObject(g);
      const c = box.getCenter(new THREE.Vector3());
      g.position.sub(c);
    }
    return g;
  }, [gltf.scene]);

  return <primitive object={root} />;
}

type GlbBoundaryProps = { children: ReactNode };

type GlbBoundaryState = { hasError: boolean };

class GlbErrorBoundary extends Component<GlbBoundaryProps, GlbBoundaryState> {
  constructor(props: GlbBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): GlbBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    if (typeof window !== "undefined") {
      console.warn("[geo-nyc] GLB render failed; showing placeholder:", error);
    }
  }

  render() {
    if (this.state.hasError) {
      return <PlaceholderBlock />;
    }
    return this.props.children;
  }
}

function SceneBody({ modelUrl }: { modelUrl: string }) {
  return (
    <>
      <color attach="background" args={["#f0ede8"]} />
      <ambientLight intensity={0.55} />
      <hemisphereLight args={["#ffffff", "#9ca3af", 0.65]} />
      <directionalLight position={[5, 8, 4]} intensity={1.0} />
      <directionalLight position={[-4, 3, -2]} intensity={0.35} />
      <Environment preset="city" />
      <GlbErrorBoundary>
        <Suspense fallback={<PlaceholderBlock />}>
          <GlbModel url={modelUrl} />
        </Suspense>
      </GlbErrorBoundary>
      <OrbitControls makeDefault enablePan minPolarAngle={0.35} maxPolarAngle={Math.PI - 0.35} />
    </>
  );
}

type SubsurfaceViewerProps = {
  pick: MapPickLocation | null;
  onClearPick?: () => void;
};

export function SubsurfaceViewer({ pick, onClearPick }: SubsurfaceViewerProps) {
  const pickKey = pick ? `${pick.lng.toFixed(5)},${pick.lat.toFixed(5)}` : "none";
  const [modelUrl, setModelUrl] = useState<string | null>(null);

  useLayoutEffect(() => {
    let cancelled = false;
    resolveGltfUrl().then((u) => {
      if (!cancelled) setModelUrl(u);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="subsurface-dock">
      <div
        className="subsurface-panel"
        role="region"
        aria-label="3D preview"
      >
        <div className="flex min-w-0 shrink-0 items-center gap-2 border-b border-line px-2.5 py-1.5">
          <Box className="h-3.5 w-3.5 shrink-0 text-muted" strokeWidth={1.75} aria-hidden />
          <div className="min-w-0 flex-1">
            <span className="block text-xs font-semibold text-ink sm:text-sm">3D</span>
            {pick ? (
              <span className="mt-0.5 block font-mono text-[10px] leading-tight text-muted sm:text-xs">
                {pick.lng.toFixed(4)}°, {pick.lat.toFixed(4)}°
              </span>
            ) : (
              <span className="mt-0.5 block text-[10px] leading-tight text-muted sm:text-xs">
                Map pick
              </span>
            )}
          </div>
          {pick && onClearPick ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onClearPick();
              }}
              className="shrink-0 rounded p-1 text-muted hover:bg-stone-100 hover:text-ink"
              aria-label="Clear"
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
            </button>
          ) : null}
        </div>
        <div className="subsurface-canvas-wrap">
          {modelUrl ? (
            <Canvas
              key={`${pickKey}-${modelUrl}`}
              className="!h-full !w-full touch-none"
              style={{ width: "100%", height: "100%" }}
              camera={{ position: [2.6, 2.0, 2.6], fov: 50 }}
              frameloop="demand"
              gl={{
                antialias: true,
                alpha: false,
                powerPreference: "default",
                toneMapping: THREE.ACESFilmicToneMapping,
                outputColorSpace: THREE.SRGBColorSpace,
                preserveDrawingBuffer: false,
                failIfMajorPerformanceCaveat: false,
              }}
              dpr={[1, 1.5]}
              onCreated={({ gl }) => {
                const canvas = gl.domElement;
                canvas.addEventListener("webglcontextlost", (e) => {
                  e.preventDefault();
                  console.warn("[geo-nyc] WebGL context lost in subsurface dock");
                });
              }}
            >
              <SceneBody modelUrl={modelUrl} />
            </Canvas>
          ) : (
            <div className="flex h-full w-full items-center justify-center bg-[#f0ede8] text-[11px] text-muted">
              Loading 3D…
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
