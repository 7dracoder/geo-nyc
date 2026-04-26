"use client";

import { OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import {
  Component,
  type ErrorInfo,
  type ReactNode,
  Suspense,
  useMemo,
} from "react";
import * as THREE from "three";
import { Box, X } from "lucide-react";
import type { MapPickLocation } from "@/types/map-pick";

/**
 * Static GLB path — served from public/exports/sample.glb.
 * No async resolution, no network probes, no WebGL context churn.
 * To update the model, replace the file and redeploy.
 */
const MODEL_URL = "/exports/sample.glb";

const PALETTE = [
  "#5C4A3A",
  "#8B6F47",
  "#D2B48C",
  "#C0B59A",
  "#a78b6f",
  "#9c8a73",
  "#7d6852",
  "#b8a98a",
];

function PlaceholderBlock() {
  return (
    <mesh>
      <boxGeometry args={[1.15, 0.75, 1.15]} />
      <meshLambertMaterial color="#a39a85" />
    </mesh>
  );
}

/**
 * Vertical exaggeration factor for geological visualization.
 * Standard practice in subsurface viz — without this, an 80m depth
 * range across a 1000m footprint looks completely flat (8% aspect ratio).
 * 8x makes the buried valley clearly visible while keeping the model
 * recognisable as terrain.
 */
const Z_EXAGGERATION = 8;

/**
 * Cheap, robust GLB rendering for the dock:
 *   - Compute vertex normals when missing (else lit materials render black).
 *   - Force DoubleSide so inconsistent winding doesn't hide layers.
 *   - Convert every material to `MeshLambertMaterial`. Lambert is the cheapest
 *     lit material in three.js: no PBR, no IBL, no PMREM cubemap allocation —
 *     which is the difference between "WebGL context lost" and a solid render
 *     when MapLibre is already eating one WebGL context on the page.
 *   - Apply vertical exaggeration so subsurface depth variation is visible.
 *   - If the source baseColor is missing or near-black, swap in a per-mesh
 *     palette color so layers are always visible.
 *   - No shadows.
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
      const fallback = PALETTE[meshIndex % PALETTE.length];
      meshIndex += 1;

      const sourceColor =
        o.material &&
        !Array.isArray(o.material) &&
        "color" in o.material &&
        (o.material as THREE.MeshStandardMaterial).color instanceof THREE.Color
          ? (o.material as THREE.MeshStandardMaterial).color.clone()
          : null;
      const useColor = sourceColor && sourceColor.r + sourceColor.g + sourceColor.b > 0.15
        ? sourceColor
        : new THREE.Color(fallback);

      const cheap = new THREE.MeshLambertMaterial({
        color: useColor,
        side: THREE.DoubleSide,
        toneMapped: true,
      });
      if (Array.isArray(o.material)) {
        o.material.forEach((m) => m?.dispose?.());
      } else {
        (o.material as THREE.Material | null | undefined)?.dispose?.();
      }
      o.material = cheap;
    });

    // Apply vertical exaggeration before fitting to view box.
    // The mesh Z axis carries depth in projected metres; stretching it
    // makes the buried-valley topography clearly visible.
    g.scale.set(1, 1, Z_EXAGGERATION);

    // Now fit the exaggerated scene into the view cube.
    const box = new THREE.Box3().setFromObject(g);
    if (!box.isEmpty()) {
      const size = box.getSize(new THREE.Vector3());
      const max = Math.max(size.x, size.y, size.z, 1e-6);
      const fitScale = 1.85 / max;
      g.scale.set(fitScale, fitScale, fitScale * Z_EXAGGERATION);
      box.setFromObject(g);
      const c = box.getCenter(new THREE.Vector3());
      g.position.sub(c);
    }

    // Recompute normals after scaling so lighting is correct.
    g.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        const geom = o.geometry as THREE.BufferGeometry | undefined;
        if (geom) geom.computeVertexNormals();
      }
    });

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
      <ambientLight intensity={0.85} />
      <hemisphereLight args={["#ffffff", "#7d7466", 0.85]} />
      <directionalLight position={[5, 8, 4]} intensity={0.9} />
      <directionalLight position={[-4, 3, -2]} intensity={0.4} />
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
            className="!h-full !w-full touch-none"
            style={{ width: "100%", height: "100%" }}
            camera={{ position: [2.6, 2.0, 2.6], fov: 50 }}
            frameloop="demand"
            gl={{
              antialias: false,
              alpha: false,
              powerPreference: "low-power",
              toneMapping: THREE.NoToneMapping,
              outputColorSpace: THREE.SRGBColorSpace,
              preserveDrawingBuffer: false,
              failIfMajorPerformanceCaveat: false,
            }}
            dpr={1}
          >
            <SceneBody modelUrl={MODEL_URL} />
          </Canvas>
        </div>
      </div>
    </div>
  );
}
