import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import {
  AdditiveBlending,
  BufferGeometry,
  CatmullRomCurve3,
  Color,
  CubicBezierCurve3,
  Float32BufferAttribute,
  type ShaderMaterial,
  TubeGeometry,
  Vector3,
} from "three";

import type { Actor } from "../data/types";
import { frame } from "../director/director";
import { CORE_RADIUS, ORBITING, SYSTEMS, systemPosition } from "../director/systems";
import type { Quality } from "../state/store";
import { CONDUIT_FRAGMENT, CONDUIT_VERTEX } from "./shaders/conduit";
import { setUniform } from "./uniforms";

const UP = new Vector3(0, 1, 0);
const ENERGY = new Color("#4d8dff").multiplyScalar(1.5);
const IDLE = new Color("#ff9a3d").multiplyScalar(0.8);

/** The spine from the core surface to a system, arcing up and out like a living fibre. */
function spine(target: Actor): CubicBezierCurve3 {
  const end = new Vector3(...systemPosition(target));
  const direction = end.clone().setY(0).normalize();
  const size = SYSTEMS[target].size;
  const start = direction.clone().multiplyScalar(CORE_RADIUS * 1.02).add(new Vector3(0, 0.25, 0));
  const finish = end.clone().sub(direction.clone().multiplyScalar(size * 1.05));
  const side = new Vector3().crossVectors(UP, direction).normalize();
  const c1 = direction.clone().multiplyScalar(CORE_RADIUS * 2.4).add(new Vector3(0, 1.3, 0)).add(side.clone().multiplyScalar(0.6));
  const c2 = finish.clone().sub(direction.clone().multiplyScalar(2.2)).add(new Vector3(0, 0.9 + end.y * 0.4, 0)).sub(side.clone().multiplyScalar(0.5));
  return new CubicBezierCurve3(start, c1, c2, finish);
}

/** Three fibres braided around the spine; they converge at both ends. */
function braid(curve: CubicBezierCurve3, fibre: number, samples: number): CatmullRomCurve3 {
  const points: Vector3[] = [];
  const phase = (fibre / 3) * Math.PI * 2;
  const twists = 2.4 + fibre * 0.35;
  const amplitude = 0.13 - fibre * 0.02;
  const frames = curve.computeFrenetFrames(samples, false);
  for (let i = 0; i <= samples; i++) {
    const u = i / samples;
    const p = curve.getPoint(u);
    const envelope = Math.pow(Math.sin(Math.PI * u), 0.7);
    const angle = u * twists * Math.PI * 2 + phase;
    const normal = frames.normals[i]!;
    const binormal = frames.binormals[i]!;
    p.addScaledVector(normal, Math.cos(angle) * amplitude * envelope);
    p.addScaledVector(binormal, Math.sin(angle) * amplitude * envelope);
    points.push(p);
  }
  return new CatmullRomCurve3(points);
}

interface ConduitProps {
  target: Actor;
  quality: Quality;
}

function Conduit({ target, quality }: ConduitProps) {
  const fibres = quality === "low" ? 2 : 3;
  const segments = quality === "low" ? 64 : 110;
  const accent = useMemo(() => new Color(SYSTEMS[target].accent).multiplyScalar(1.3), [target]);
  const { geometries, sway } = useMemo(() => {
    const curve = spine(target);
    const direction = new Vector3(...systemPosition(target)).setY(0).normalize();
    const sway = new Vector3().crossVectors(UP, direction).normalize().add(new Vector3(0, 0.6, 0));
    const geometries: BufferGeometry[] = [];
    for (let fibre = 0; fibre < fibres; fibre++) {
      const radius = [0.03, 0.02, 0.014][fibre] ?? 0.014;
      geometries.push(new TubeGeometry(braid(curve, fibre, 90), segments, radius, 6, false));
    }
    return { geometries, sway };
  }, [target, fibres, segments]);
  const uniforms = useMemo(
    () =>
      Array.from({ length: fibres }, (_, fibre) => ({
        uTime: { value: 0 },
        uPhase: { value: fibre * 1.7 + SYSTEMS[target].angle * 0.05 },
        uSway: { value: sway },
        uLevel: { value: 0 },
        uPulse: { value: -1 },
        uPulseStrength: { value: 0 },
        uIdle: { value: 1.0 - fibre * 0.2 },
        uIdleColor: { value: IDLE },
        uAccent: { value: accent },
        uEnergy: { value: ENERGY },
      })),
    [accent, fibres, sway, target],
  );
  const materials = useRef<Array<ShaderMaterial | null>>([]);
  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const level = frame.levels[target] ?? 0;
    let pulse = -1;
    let strength = 0;
    for (const item of frame.pulses) {
      if (item.target === target && item.progress > pulse) {
        pulse = item.progress;
        strength = item.strength;
      }
    }
    for (const material of materials.current) {
      setUniform(material, "uTime", t);
      setUniform(material, "uLevel", level);
      setUniform(material, "uPulse", pulse);
      setUniform(material, "uPulseStrength", strength);
    }
  });
  return (
    <group>
      {geometries.map((geometry, fibre) => (
        <mesh key={fibre} geometry={geometry} raycast={() => null} renderOrder={1}>
          <shaderMaterial
            ref={(material) => {
              materials.current[fibre] = material;
            }}
            vertexShader={CONDUIT_VERTEX}
            fragmentShader={CONDUIT_FRAGMENT}
            uniforms={uniforms[fibre]}
            transparent
            depthWrite={false}
            blending={AdditiveBlending}
          />
        </mesh>
      ))}
    </group>
  );
}

/** The dashed link from Memory to the unbuilt Engram layer. */
function EngramLink() {
  const geometry = useMemo(() => {
    const from = new Vector3(...systemPosition("memory"));
    const to = new Vector3(...systemPosition("engram"));
    const points: number[] = [];
    const steps = 40;
    for (let i = 0; i < steps; i++) {
      if (i % 2) continue;
      const a = from.clone().lerp(to, i / steps);
      const b = from.clone().lerp(to, (i + 1) / steps);
      points.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(points, 3));
    return g;
  }, []);
  return (
    <lineSegments geometry={geometry} raycast={() => null}>
      <lineBasicMaterial color="#6b6f78" transparent opacity={0.35} depthWrite={false} />
    </lineSegments>
  );
}

/** Atlas: the control-plane boundary every conduit passes through. Static architecture. */
function AtlasRing() {
  const { ring, ticks } = useMemo(() => {
    const radius = 4.3;
    const ringPoints: number[] = [];
    const tickPoints: number[] = [];
    const steps = 240;
    for (let i = 0; i < steps; i++) {
      if (i % 6 === 5) continue;
      const a = (i / steps) * Math.PI * 2;
      const b = ((i + 1) / steps) * Math.PI * 2;
      ringPoints.push(Math.cos(a) * radius, 0, Math.sin(a) * radius, Math.cos(b) * radius, 0, Math.sin(b) * radius);
    }
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const inner = i % 6 === 0 ? radius - 0.22 : radius - 0.08;
      tickPoints.push(Math.cos(a) * inner, 0, Math.sin(a) * inner, Math.cos(a) * radius, 0, Math.sin(a) * radius);
    }
    const ring = new BufferGeometry();
    ring.setAttribute("position", new Float32BufferAttribute(ringPoints, 3));
    const ticks = new BufferGeometry();
    ticks.setAttribute("position", new Float32BufferAttribute(tickPoints, 3));
    return { ring, ticks };
  }, []);
  return (
    <group rotation={[0.04, 0, 0]}>
      <lineSegments geometry={ring} raycast={() => null}>
        <lineBasicMaterial color="#ffb45a" transparent opacity={0.22} depthWrite={false} />
      </lineSegments>
      <lineSegments geometry={ticks} raycast={() => null}>
        <lineBasicMaterial color="#ffb45a" transparent opacity={0.3} depthWrite={false} />
      </lineSegments>
      <Html position={[0, 0, 4.3]} center zIndexRange={[3, 0]} style={{ pointerEvents: "none" }}>
        <div className="nx-atlas-label">ATLAS · POLICY · APPROVAL · AUDIT</div>
      </Html>
    </group>
  );
}

export function Conduits({ quality }: { quality: Quality }) {
  return (
    <group>
      <AtlasRing />
      {ORBITING.map((target) => (
        <Conduit key={target} target={target} quality={quality} />
      ))}
      <EngramLink />
    </group>
  );
}
