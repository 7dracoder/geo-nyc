"use client";

import { Center, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import {
  Component,
  type ErrorInfo,
  type ReactNode,
  Suspense,
  useMemo,
} from "react";
import { Box, X } from "lucide-react";
import type { MapPickLocation } from "@/types/map-pick";

const GLB_URL = "/exports/sample.glb";

/** Visible placeholder when GLB is missing or still loading. */
function PlaceholderBlock() {
  return (
    <mesh castShadow receiveShadow>
      <boxGeometry args={[1.15, 0.75, 1.15]} />
      <meshStandardMaterial
        color="#78716c"
        roughness={0.42}
        metalness={0.12}
        envMapIntensity={0.6}
      />
    </mesh>
  );
}

function GlbModel() {
  const gltf = useGLTF(GLB_URL);
  const scene = useMemo(() => gltf.scene.clone(), [gltf.scene]);
  return (
    <Center>
      <primitive object={scene} />
    </Center>
  );
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

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* missing GLB / decode errors — placeholder stays up */
  }

  render() {
    if (this.state.hasError) {
      return <PlaceholderBlock />;
    }
    return this.props.children;
  }
}

type SubsurfaceViewerProps = {
  pick: MapPickLocation | null;
  onClearPick?: () => void;
};

export function SubsurfaceViewer({ pick, onClearPick }: SubsurfaceViewerProps) {
  const pickKey = pick ? `${pick.lng.toFixed(5)},${pick.lat.toFixed(5)}` : "none";

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
          <Canvas
            key={pickKey}
            className="!h-full !w-full touch-none"
            style={{ width: "100%", height: "100%" }}
            camera={{ position: [2.4, 1.9, 2.4], fov: 45 }}
            gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}
            dpr={[1, 2]}
          >
            <color attach="background" args={["#f0ede8"]} />
            <ambientLight intensity={0.82} />
            <directionalLight position={[4, 6, 3]} intensity={0.95} />
            <GlbErrorBoundary key={`glb-${pickKey}`}>
              <Suspense fallback={<PlaceholderBlock />}>
                <GlbModel />
              </Suspense>
            </GlbErrorBoundary>
            <OrbitControls makeDefault enablePan />
          </Canvas>
        </div>
      </div>
    </div>
  );
}
