import { Html, PerformanceMonitor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, Noise, ToneMapping, Vignette } from "@react-three/postprocessing";
import { BlendFunction, ToneMappingMode } from "postprocessing";
import { useEffect, useMemo, useRef } from "react";
import { ACESFilmicToneMapping, SRGBColorSpace } from "three";

import type { SystemId, SystemView } from "../data/types";
import { updateFrame } from "../director/director";
import { CORE_RADIUS, ORBITING, stopIndexFor } from "../director/systems";
import { type Quality, useStore } from "../state/store";
import { CameraRig } from "./CameraRig";
import { Conduits } from "./Conduits";
import { Environment } from "./Environment";
import { NexusCore } from "./NexusCore";
import { EngramGhost, type LabelInfo, OrbLabel, SystemOrb, useLabelLit } from "./SystemOrb";

function Director({ reducedMotion }: { reducedMotion: boolean }) {
  // Runs before every other frame callback: composes the scene state once per frame.
  useFrame((state) => updateFrame(state.clock.elapsedTime, performance.now()), -1);
  const invalidate = useThree((state) => state.invalidate);
  // Reduced motion renders on demand: redraw when NEXUS state or replay time changes.
  useEffect(() => {
    if (!reducedMotion) return;
    const unsubscribe = useStore.subscribe(() => invalidate());
    const timer = window.setInterval(() => {
      if (useStore.getState().replay.playing) invalidate();
    }, 250);
    return () => {
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [invalidate, reducedMotion]);
  return null;
}

/** Capture-only: publish renderer cost so visual tests can record it. */
function RendererStats() {
  const enabled = useMemo(() => new URLSearchParams(window.location.search).has("capture"), []);
  const samples = useRef<number[]>([]);
  useFrame((state, delta) => {
    if (!enabled) return;
    // Count the whole frame (scene plus post passes): reset here, read next frame.
    state.gl.info.autoReset = false;
    samples.current.push(delta);
    if (samples.current.length > 120) samples.current.shift();
    const info = state.gl.info;
    const mean = samples.current.reduce((sum, value) => sum + value, 0) / samples.current.length;
    (window as unknown as { __nexusRenderer: object }).__nexusRenderer = {
      calls: info.render.calls,
      triangles: info.render.triangles,
      points: info.render.points,
      lines: info.render.lines,
      geometries: info.memory.geometries,
      textures: info.memory.textures,
      programs: info.programs?.length ?? 0,
      meanFrameMs: Math.round(mean * 10000) / 10,
      dpr: state.gl.getPixelRatio(),
    };
    info.reset();
  }, -2);
  return null;
}

function Effects({ quality }: { quality: Quality }) {
  if (quality === "low") return null;
  return (
    <EffectComposer multisampling={0} enableNormalPass={false}>
      <Bloom
        mipmapBlur
        intensity={quality === "high" ? 1.15 : 0.9}
        luminanceThreshold={0.42}
        luminanceSmoothing={0.22}
        radius={0.78}
      />
      {/* Tone map first: grain and vignette must blend in display range, not HDR. */}
      <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
      <Vignette offset={0.26} darkness={0.78} />
      <Noise premultiply blendFunction={BlendFunction.SOFT_LIGHT} opacity={0.32} />
    </EffectComposer>
  );
}

function useLabels(): Record<SystemId, LabelInfo> {
  const systems = useStore((state) => state.snapshot?.systems);
  const mode = useStore((state) => state.mode);
  const scripted = useStore((state) => state.replay.episode?.scripted_systems);
  return useMemo(() => {
    const byId = new Map<SystemId, SystemView>((systems ?? []).map((item) => [item.id, item]));
    const info = {} as Record<SystemId, LabelInfo>;
    const ids: SystemId[] = ["nexus", ...ORBITING, "engram"];
    for (const id of ids) {
      const view = byId.get(id);
      info[id] = {
        status: view?.status ?? (id === "engram" ? "not_built" : "dormant"),
        detail: view?.status_detail ?? "",
        scripted: mode === "replay" && (scripted ?? []).includes(id),
        replay: mode === "replay" && id !== "engram",
      };
    }
    return info;
  }, [systems, mode, scripted]);
}

function World({ quality, reducedMotion }: { quality: Quality; reducedMotion: boolean }) {
  const labels = useLabels();
  const goTo = useStore((state) => state.goTo);
  const coreLabel = useRef<HTMLDivElement>(null);
  const coreHovered = useRef(false);
  useLabelLit("nexus", coreLabel, coreHovered);
  return (
    <>
      <Director reducedMotion={reducedMotion} />
      <Environment quality={quality} />
      <NexusCore quality={quality} onSelect={() => goTo(stopIndexFor("nexus"))} />
      <Html position={[CORE_RADIUS * 0.78, CORE_RADIUS * 1.05, 0]} zIndexRange={[4, 0]} style={{ pointerEvents: "none" }}>
        <OrbLabel id="nexus" info={labels.nexus} labelRef={coreLabel} />
      </Html>
      <Conduits quality={quality} />
      {ORBITING.map((id) => (
        <SystemOrb
          key={id}
          id={id as Exclude<SystemId, "nexus" | "engram">}
          quality={quality}
          info={labels[id]}
          onSelect={() => goTo(stopIndexFor(id))}
        />
      ))}
      <EngramGhost info={labels.engram} />
      <CameraRig reducedMotion={reducedMotion} />
      <Effects quality={quality} />
      <RendererStats />
    </>
  );
}

export default function Scene() {
  const quality = useStore((state) => state.quality);
  const reducedMotion = useStore((state) => state.reducedMotion);
  const setQuality = useStore((state) => state.setQuality);
  const dpr: [number, number] = quality === "high" ? [1, 1.5] : quality === "medium" ? [1, 1.25] : [0.75, 1];
  return (
    <Canvas
      dpr={dpr}
      frameloop={reducedMotion ? "demand" : "always"}
      camera={{ fov: 38, near: 0.1, far: 900, position: [0, 8.4, 19.5] }}
      gl={{
        antialias: quality !== "low",
        powerPreference: "high-performance",
        toneMapping: ACESFilmicToneMapping,
        outputColorSpace: SRGBColorSpace,
        preserveDrawingBuffer: new URLSearchParams(window.location.search).has("capture"),
      }}
      onPointerMissed={() => {
        document.body.style.cursor = "";
      }}
    >
      <PerformanceMonitor
        flipflops={2}
        onDecline={() => setQuality(quality === "high" ? "medium" : "low")}
      />
      <World quality={quality} reducedMotion={reducedMotion} />
    </Canvas>
  );
}
