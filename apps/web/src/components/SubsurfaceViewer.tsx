"use client";

import { Edges, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import {
  Component,
  type ErrorInfo,
  type ReactNode,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as THREE from "three";
import { Box, Loader2, X } from "lucide-react";
import { createRun, proxiedMeshUrl } from "@/lib/api";
import type { MapPickLocation } from "@/types/map-pick";

/** Fallback GLB when no run has been triggered yet. */
const DEFAULT_MODEL_URL = "/exports/sample.glb";

/** Used only when a mesh is missing both vertex colours AND material colour. */
const PALETTE_FALLBACK = [
  "#5C4A3A",
  "#8B6F47",
  "#D2B48C",
  "#C0B59A",
  "#a78b6f",
  "#9c8a73",
];

/** A layer surfaced from the GLB that we feed into the legend. */
type LayerInfo = {
  surfaceId: string;
  name: string;
  colorHex: string;
};

function PlaceholderBlock() {
  return (
    <mesh>
      <boxGeometry args={[1.15, 0.75, 1.15]} />
      <meshLambertMaterial color="#a39a85" />
    </mesh>
  );
}

/**
 * Render the GLB scene with its baked per-vertex colours preserved,
 * plus a wireframe bounding box and axis labels so the model reads as
 * a geological block model rather than a featureless slab.
 */
function GlbModel({
  url,
  onLayers,
}: {
  url: string;
  onLayers?: (layers: LayerInfo[]) => void;
}) {
  const gltf = useGLTF(url);

  const { root, box, layers } = useMemo(() => {
    const g = gltf.scene.clone(true);
    const found: LayerInfo[] = [];
    let meshIndex = 0;

    g.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const geom = o.geometry as THREE.BufferGeometry;
      if (!geom.attributes.normal) geom.computeVertexNormals();

      const colorAttr = geom.attributes.color as THREE.BufferAttribute | undefined;
      let colorHex = PALETTE_FALLBACK[meshIndex % PALETTE_FALLBACK.length];

      if (colorAttr && colorAttr.itemSize >= 3) {
        // glTF stores colours as floats in [0, 1] for the COLOR_0
        // attribute trimesh emits — sample the first vertex to drive
        // the legend swatch.
        const r = clamp01(colorAttr.getX(0));
        const gg = clamp01(colorAttr.getY(0));
        const b = clamp01(colorAttr.getZ(0));
        colorHex = rgbToHex(r, gg, b);
      } else if (
        o.material &&
        !Array.isArray(o.material) &&
        "color" in o.material
      ) {
        const c = (o.material as THREE.MeshStandardMaterial).color;
        if (c instanceof THREE.Color && c.r + c.g + c.b > 0.05) {
          colorHex = `#${c.getHexString()}`;
        }
      }

      const fresh = new THREE.MeshLambertMaterial({
        color: new THREE.Color(colorHex),
        vertexColors: !!colorAttr,
        side: THREE.DoubleSide,
        toneMapped: false,
      });

      if (Array.isArray(o.material)) {
        o.material.forEach((m) => m?.dispose?.());
      } else {
        (o.material as THREE.Material | null | undefined)?.dispose?.();
      }
      o.material = fresh;
      o.castShadow = false;
      o.receiveShadow = false;

      found.push({
        surfaceId: o.name || `layer_${meshIndex}`,
        name: humanise(o.name) || `Layer ${meshIndex + 1}`,
        colorHex,
      });
      meshIndex += 1;
    });

    // Recompute normals after material swap.
    g.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        const geom = o.geometry as THREE.BufferGeometry | undefined;
        geom?.computeVertexNormals();
      }
    });

    // Auto-fit the model into a clean unit-ish box so the camera
    // never has to dynamically chase huge UTM coordinates. The
    // backend has already applied a 5× vertical exaggeration, so we
    // pass through the GLB's aspect untouched here.
    const initBox = new THREE.Box3().setFromObject(g);
    if (!initBox.isEmpty()) {
      const size = initBox.getSize(new THREE.Vector3());
      const max = Math.max(size.x, size.y, size.z, 1e-6);
      const fitScale = 2.0 / max;
      g.scale.setScalar(fitScale);
      const fittedBox = new THREE.Box3().setFromObject(g);
      const c = fittedBox.getCenter(new THREE.Vector3());
      g.position.sub(c);
    }

    const finalBox = new THREE.Box3().setFromObject(g);
    return { root: g, box: finalBox, layers: found };
  }, [gltf.scene]);

  // Hand the layers to the parent for the legend without triggering a
  // re-render of the GLB itself.
  useEffect(() => {
    onLayers?.(layers);
  }, [layers, onLayers]);

  return (
    <>
      <primitive object={root} />
      <BoundingBoxFrame box={box} />
      <AxisLabels box={box} />
    </>
  );
}

/** Wireframe outline + light ground grid that matches the reference image. */
function BoundingBoxFrame({ box }: { box: THREE.Box3 }) {
  const geom = useMemo(() => {
    if (box.isEmpty()) return null;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const g = new THREE.BoxGeometry(size.x, size.y, size.z);
    g.translate(center.x, center.y, center.z);
    return g;
  }, [box]);
  if (!geom) return null;
  return (
    <mesh geometry={geom}>
      <meshBasicMaterial visible={false} />
      <Edges threshold={1} color="#1f2937" />
    </mesh>
  );
}

/** X / Y / Z axis labels at the box corners. */
function AxisLabels({ box }: { box: THREE.Box3 }) {
  if (box.isEmpty()) return null;
  const min = box.min;
  const max = box.max;
  const pad = 0.08;
  const xPos: [number, number, number] = [max.x + pad, min.y, max.z];
  const yPos: [number, number, number] = [min.x, max.y + pad, max.z];
  const zPos: [number, number, number] = [min.x, min.y, max.z + pad];

  return (
    <group>
      <Html center position={xPos} className="axis-label">
        X
      </Html>
      <Html center position={yPos} className="axis-label">
        Z
      </Html>
      <Html center position={zPos} className="axis-label">
        Y
      </Html>
    </group>
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
  componentDidCatch(error: Error, _info: ErrorInfo) {
    if (typeof window !== "undefined") {
      console.warn("[geo-nyc] GLB render failed; showing placeholder:", error);
    }
  }
  render() {
    if (this.state.hasError) return <PlaceholderBlock />;
    return this.props.children;
  }
}

function SceneBody({
  modelUrl,
  onLayers,
}: {
  modelUrl: string;
  onLayers?: (layers: LayerInfo[]) => void;
}) {
  return (
    <>
      <color attach="background" args={["#f8f7f2"]} />
      <ambientLight intensity={0.6} />
      <hemisphereLight args={["#ffffff", "#8a8474", 0.55]} />
      <directionalLight position={[4, 6, 5]} intensity={0.95} />
      <directionalLight position={[-4, 3, -3]} intensity={0.35} />
      <GlbErrorBoundary>
        <Suspense fallback={<PlaceholderBlock />}>
          <GlbModel url={modelUrl} onLayers={onLayers} />
        </Suspense>
      </GlbErrorBoundary>
      <OrbitControls
        makeDefault
        enablePan
        minPolarAngle={0.2}
        maxPolarAngle={Math.PI - 0.2}
      />
    </>
  );
}

type SubsurfaceViewerProps = {
  pick: MapPickLocation | null;
  onClearPick?: () => void;
};

export function SubsurfaceViewer({ pick, onClearPick }: SubsurfaceViewerProps) {
  const [modelUrl, setModelUrl] = useState(DEFAULT_MODEL_URL);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layers, setLayers] = useState<LayerInfo[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!pick) return;

    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    let cancelled = false;
    setLoading(true);
    setError(null);

    createRun(pick.lng, pick.lat)
      .then((manifest) => {
        if (cancelled) return;
        const mesh = manifest.artifacts.find(
          (a) => a.kind === "mesh" && a.filename.endsWith(".glb"),
        );
        if (mesh?.url) {
          const url = proxiedMeshUrl(mesh.url);
          useGLTF.clear(url);
          setModelUrl(url);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        console.warn("[geo-nyc] run failed:", err);
        setError("Model generation failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [pick?.lng, pick?.lat]);

  const onClear = useCallback(() => {
    abortRef.current?.abort();
    setModelUrl(DEFAULT_MODEL_URL);
    setError(null);
    setLoading(false);
    setLayers([]);
    onClearPick?.();
  }, [onClearPick]);

  const handleLayers = useCallback((next: LayerInfo[]) => {
    setLayers(next);
  }, []);

  return (
    <div className="subsurface-dock">
      <div className="subsurface-panel" role="region" aria-label="3D preview">
        <div className="flex min-w-0 shrink-0 items-center gap-2 border-b border-line px-2.5 py-1.5">
          <Box
            className="h-3.5 w-3.5 shrink-0 text-muted"
            strokeWidth={1.75}
            aria-hidden
          />
          <div className="min-w-0 flex-1">
            <span className="block text-xs font-semibold text-ink sm:text-sm">
              3D
            </span>
            {pick ? (
              <span className="mt-0.5 block font-mono text-[10px] leading-tight text-muted sm:text-xs">
                {pick.lng.toFixed(4)}°, {pick.lat.toFixed(4)}°
              </span>
            ) : (
              <span className="mt-0.5 block text-[10px] leading-tight text-muted sm:text-xs">
                Click the map
              </span>
            )}
          </div>
          {loading && (
            <Loader2
              className="h-3.5 w-3.5 shrink-0 animate-spin text-muted"
              strokeWidth={2}
              aria-label="Generating model…"
            />
          )}
          {pick && !loading ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
              className="shrink-0 rounded p-1 text-muted hover:bg-stone-100 hover:text-ink"
              aria-label="Clear"
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
            </button>
          ) : null}
        </div>
        <div className="subsurface-canvas-wrap">
          {error ? (
            <div className="flex h-full w-full items-center justify-center bg-[#f0ede8] text-[11px] text-red-500">
              {error}
            </div>
          ) : (
            <>
              <Canvas
                key={modelUrl}
                className="!h-full !w-full touch-none"
                style={{ width: "100%", height: "100%" }}
                camera={{ position: [2.4, 1.9, 2.4], fov: 45 }}
                frameloop="demand"
                gl={{
                  antialias: true,
                  alpha: false,
                  powerPreference: "default",
                  toneMapping: THREE.NoToneMapping,
                  outputColorSpace: THREE.SRGBColorSpace,
                  preserveDrawingBuffer: false,
                  failIfMajorPerformanceCaveat: false,
                }}
                dpr={[1, 1.5]}
              >
                <SceneBody modelUrl={modelUrl} onLayers={handleLayers} />
              </Canvas>
              {layers.length > 0 && <Legend layers={layers} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Legend({ layers }: { layers: LayerInfo[] }) {
  return (
    <div className="subsurface-legend" aria-label="Layer legend">
      <div className="subsurface-legend-title">Formations</div>
      {layers.map((l) => (
        <div key={l.surfaceId} className="subsurface-legend-row">
          <span
            className="subsurface-legend-swatch"
            style={{ background: l.colorHex }}
            aria-hidden
          />
          <span className="subsurface-legend-name">{l.name}</span>
        </div>
      ))}
    </div>
  );
}

function clamp01(n: number) {
  return Math.max(0, Math.min(1, n));
}

function rgbToHex(r: number, g: number, b: number): string {
  const to = (c: number) =>
    Math.round(c * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

function humanise(name: string | undefined | null): string {
  if (!name) return "";
  // Drop the "S_R_" / "S_" prefix used by the backend for surface ids.
  const stripped = name.replace(/^S_(R_)?/, "").replace(/_/g, " ");
  return stripped.replace(/\b\w/g, (c) => c.toUpperCase());
}
