import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { type Ref, type RefObject, useMemo, useRef, useState } from "react";
import {
  AdditiveBlending,
  Color,
  DoubleSide,
  type Group,
  type Mesh,
  type MeshBasicMaterial,
  Vector3,
} from "three";

import type { SystemId, SystemStatus } from "../data/types";
import { frame } from "../director/director";
import { STOPS, SYSTEMS, systemPosition } from "../director/systems";
import { type Quality, useStore } from "../state/store";
import { OrbBody } from "./OrbBody";

const DORMANT = new Color("#4a4946");

/** Identity structures: each system reads differently even when dark. */
function Structure({ id, radius, accent }: { id: SystemId; radius: number; accent: Color }) {
  const group = useRef<Group>(null);
  const tinted = useRef<MeshBasicMaterial[]>([]);
  const color = useMemo(() => new Color(), []);
  const register = (material: MeshBasicMaterial | null) => {
    if (material && !tinted.current.includes(material)) tinted.current.push(material);
  };
  const sweep = useRef<Mesh>(null);
  const band = useRef<Mesh>(null);
  useFrame((state, delta) => {
    const level = frame.levels[id] ?? 0;
    color.copy(DORMANT).lerp(accent, level).multiplyScalar(0.55 + level * 1.4);
    for (const material of tinted.current) {
      material.color.copy(color);
      material.opacity = 0.28 + level * 0.6;
    }
    const g = group.current;
    if (!g) return;
    const t = state.clock.elapsedTime;
    const speed = 1 + level * 2.5;
    if (id === "patchforge" || id === "memory") g.rotation.y += delta * 0.12 * speed;
    if (id === "resident_engineer") g.rotation.y += delta * 0.35 * speed;
    if (sweep.current) sweep.current.rotation.z -= delta * 0.9 * speed;
    if (band.current) band.current.position.y = Math.sin(t * 0.8 * speed) * radius * 0.85;
  });

  switch (id) {
    case "aegisops":
      return (
        <group ref={group} rotation={[-Math.PI / 2, 0, 0]}>
          <mesh raycast={() => null}>
            <ringGeometry args={[radius * 1.45, radius * 1.47, 96]} />
            <meshBasicMaterial ref={register} transparent depthWrite={false} side={DoubleSide} blending={AdditiveBlending} />
          </mesh>
          <mesh raycast={() => null}>
            <ringGeometry args={[radius * 1.75, radius * 1.76, 96]} />
            <meshBasicMaterial ref={register} transparent depthWrite={false} side={DoubleSide} blending={AdditiveBlending} />
          </mesh>
          <mesh ref={sweep} raycast={() => null}>
            <ringGeometry args={[radius * 1.2, radius * 1.75, 48, 1, 0, Math.PI / 5]} />
            <meshBasicMaterial ref={register} transparent depthWrite={false} side={DoubleSide} blending={AdditiveBlending} />
          </mesh>
        </group>
      );
    case "patchforge":
      return (
        <group ref={group}>
          <mesh raycast={() => null}>
            <icosahedronGeometry args={[radius * 1.42, 0]} />
            <meshBasicMaterial ref={register} wireframe transparent depthWrite={false} blending={AdditiveBlending} />
          </mesh>
        </group>
      );
    case "sentinelqa":
      return (
        <group ref={group}>
          <mesh raycast={() => null}>
            <sphereGeometry args={[radius * 1.38, 12, 1]} />
            <meshBasicMaterial ref={register} wireframe transparent depthWrite={false} blending={AdditiveBlending} />
          </mesh>
          <mesh ref={band} rotation={[Math.PI / 2, 0, 0]} raycast={() => null}>
            <torusGeometry args={[radius * 1.3, 0.008, 6, 96]} />
            <meshBasicMaterial ref={register} transparent depthWrite={false} blending={AdditiveBlending} />
          </mesh>
        </group>
      );
    case "resident_engineer":
      return (
        <group ref={group}>
          {[0, 1].map((index) => (
            <group key={index} rotation={[index ? 0.9 : -0.35, 0, index ? 0.4 : -0.2]}>
              <mesh raycast={() => null}>
                <torusGeometry args={[radius * (1.45 + index * 0.25), 0.004, 4, 128]} />
                <meshBasicMaterial ref={register} transparent depthWrite={false} blending={AdditiveBlending} />
              </mesh>
              <mesh position={[radius * (1.45 + index * 0.25), 0, 0]} raycast={() => null}>
                <sphereGeometry args={[0.06, 12, 8]} />
                <meshBasicMaterial ref={register} transparent depthWrite={false} blending={AdditiveBlending} />
              </mesh>
            </group>
          ))}
        </group>
      );
    case "memory":
      return (
        <group ref={group}>
          {[1.3, 1.55].map((scale, index) => (
            <mesh key={scale} rotation={[index * 0.6, 0, index * 0.3]} raycast={() => null}>
              <sphereGeometry args={[radius * scale, 16, 8]} />
              <meshBasicMaterial ref={register} wireframe transparent depthWrite={false} blending={AdditiveBlending} />
            </mesh>
          ))}
        </group>
      );
    default:
      return null;
  }
}

export interface LabelInfo {
  status: SystemStatus;
  detail: string;
  scripted: boolean;
  replay: boolean;
}

const BODIES: Array<{ id: SystemId; center: Vector3; radius: number }> = (
  ["nexus", "aegisops", "patchforge", "sentinelqa", "resident_engineer", "memory"] as SystemId[]
).map((id) => ({ id, center: new Vector3(...systemPosition(id)), radius: SYSTEMS[id].size * 1.05 }));

const toTarget = new Vector3();
const toBody = new Vector3();
const projected = new Vector3();

/** True when another body sits between the camera and this system's orb. */
function occluded(id: SystemId, camera: Vector3): boolean {
  const target = BODIES.find((body) => body.id === id);
  if (!target) return false;
  toTarget.copy(target.center).sub(camera);
  const distance = toTarget.length();
  toTarget.divideScalar(distance);
  for (const body of BODIES) {
    if (body.id === id) continue;
    toBody.copy(body.center).sub(camera);
    const along = toBody.dot(toTarget);
    if (along <= 0 || along >= distance) continue;
    const miss = toBody.lengthSq() - along * along;
    if (miss < body.radius * body.radius) return true;
  }
  return false;
}

/** Runs inside the Canvas: lights, dims, or hides a label without re-rendering React. */
export function useLabelLit(id: SystemId, label: RefObject<HTMLDivElement | null>, hovered: RefObject<boolean>) {
  const last = useRef("");
  useFrame((state) => {
    const element = label.current;
    if (!element) return;
    const lit = (frame.levels[id] ?? 0) > 0.35 || hovered.current === true;
    const focus = STOPS[useStore.getState().stop]?.system ?? null;
    const hidden = id !== "engram" && occluded(id, state.camera.position);
    // Labels that drift under the HUD's text columns step back so the text stays legible.
    projected.set(...systemPosition(id)).project(state.camera);
    const x = (projected.x + 1) / 2;
    const y = (1 - projected.y) / 2;
    const underText = state.size.width > 820 && (x < 0.21 || x > 0.69) && y > 0.08 && y < 0.86;
    let opacity = focus === null || focus === id || lit ? 1 : id === "nexus" ? 0.75 : 0.45;
    if (underText && focus !== id && !lit) opacity = Math.min(opacity, 0.14);
    if (hidden) opacity = 0;
    const key = `${lit}:${opacity}`;
    if (key === last.current) return;
    last.current = key;
    element.dataset.lit = String(lit);
    element.style.opacity = String(opacity);
  });
}

/** Plain DOM: drei's Html renders it in a separate React root, outside the Canvas. */
export function OrbLabel({ id, info, labelRef }: { id: SystemId; info: LabelInfo; labelRef?: Ref<HTMLDivElement> }) {
  const meta = SYSTEMS[id];
  const stateText = info.replay
    ? "Replay"
    : info.status === "not_built"
      ? "Not built"
      : info.status;
  return (
    <div
      ref={labelRef}
      className="nx-orb-label"
      data-status={info.status}
      data-core={id === "nexus"}
      data-ghost={id === "engram"}
      style={{ ["--sys-color" as string]: meta.accent }}
    >
      <div className="nx-orb-label__code">
        <span>{meta.designation}</span>
        <span>{id === "engram" ? "PHASE 13" : "SYS"}</span>
      </div>
      <div className="nx-orb-label__name">{meta.short}</div>
      <div className="nx-orb-label__state" data-status={info.replay ? "replay" : info.status}>
        {stateText}
        {info.scripted ? <span className="nx-orb-label__scripted">SCRIPTED</span> : null}
      </div>
    </div>
  );
}

export function SystemOrb({
  id,
  quality,
  info,
  onSelect,
}: {
  id: Exclude<SystemId, "nexus" | "engram">;
  quality: Quality;
  info: LabelInfo;
  onSelect: () => void;
}) {
  const meta = SYSTEMS[id];
  const [hovered, setHovered] = useState(false);
  const hoveredRef = useRef(false);
  const labelRef = useRef<HTMLDivElement>(null);
  useLabelLit(id, labelRef, hoveredRef);
  const accent = useMemo(() => new Color(meta.accent), [meta.accent]);
  const position = systemPosition(id);
  const segments = quality === "low" ? 40 : 72;
  return (
    <group position={position}>
      <OrbBody
        radius={meta.size}
        accent={meta.accent}
        seed={meta.angle * 0.13}
        segments={segments}
        onSelect={onSelect}
        onHover={(value) => {
          setHovered(value);
          hoveredRef.current = value;
          document.body.style.cursor = value ? "pointer" : "";
        }}
        drive={() => ({
          level: Math.min(1, (frame.levels[id] ?? 0) + (hovered ? 0.18 : 0)),
          charge: 0,
          flash: 0,
          attention: info.status === "attention" && !info.replay ? 0.6 : 0,
        })}
      />
      <Structure id={id} radius={meta.size} accent={accent} />
      <Html position={[meta.size * 0.8, meta.size * 1.05, 0]} zIndexRange={[4, 0]} style={{ pointerEvents: "none" }}>
        <OrbLabel id={id} info={info} labelRef={labelRef} />
      </Html>
    </group>
  );
}

/** Engram is on the roadmap, not in the code: a dashed ghost, never lit. */
export function EngramGhost({ info }: { info: LabelInfo }) {
  const meta = SYSTEMS.engram;
  const position = systemPosition("engram");
  const group = useRef<Group>(null);
  useFrame((_, delta) => {
    if (group.current) group.current.rotation.y += delta * 0.05;
  });
  return (
    <group position={position}>
      <group ref={group}>
        <mesh raycast={() => null}>
          <sphereGeometry args={[meta.size, 18, 10]} />
          <meshBasicMaterial color="#565a62" wireframe transparent opacity={0.22} depthWrite={false} />
        </mesh>
      </group>
      <Html position={[meta.size * 0.8, meta.size * 1.05, 0]} zIndexRange={[4, 0]} style={{ pointerEvents: "none" }}>
        <OrbLabel id="engram" info={info} />
      </Html>
    </group>
  );
}
