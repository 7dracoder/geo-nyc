"use client";

import { OrbitControls, useGLTF } from "@react-three/drei";
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

function PlaceholderBlock() {
  return (
    <mesh castShadow receiveShadow>
      <boxGeometry args={[1.15, 0.75, 1.15]} />
      <meshStandardMaterial color="#78716c" roughness={0.45} metalness={0.08} />
    </mesh>
  );
}

function GlbModel({ url }: { url: string }) {
  const gltf = useGLTF(url);
  const root = useMemo(() => {
    const g = gltf.scene.clone();
    g.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.castShadow = true;
        o.receiveShadow = true;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const mat of mats) {
          if (mat instanceof THREE.MeshStandardMaterial) {
            mat.toneMapped = true;
          }
        }
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

  componentDidCatch(_error: Error, _info: ErrorInfo) {}

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
      <ambientLight intensity={0.9} />
      <directionalLight position={[5, 8, 4]} intensity={1.05} castShadow />
      <directionalLight position={[-4, 3, -2]} intensity={0.35} />
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
              shadows
              gl={{
                antialias: true,
                alpha: false,
                powerPreference: "high-performance",
                toneMapping: THREE.ACESFilmicToneMapping,
                outputColorSpace: THREE.SRGBColorSpace,
              }}
              dpr={[1, 2]}
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
