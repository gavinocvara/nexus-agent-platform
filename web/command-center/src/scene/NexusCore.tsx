import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  type Group,
  type Points,
  type ShaderMaterial,
} from "three";

import { frame } from "../director/director";
import { CORE_RADIUS } from "../director/systems";
import type { Quality } from "../state/store";
import { OrbBody } from "./OrbBody";
import { setUniform } from "./uniforms";

// Fragmented particle rings around the core: the instrument read-out of the reference HUD
// orb. Idle rings are amber; charge pulls them toward the blue activity energy.

const RING_VERTEX = /* glsl */ `
attribute float aBright;
attribute float aSize;
uniform float uPixelRatio;
uniform float uTime;
varying float vBright;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vBright = aBright * (0.75 + 0.25 * sin(uTime * 1.7 + position.x * 3.1 + position.z * 2.3));
  gl_PointSize = aSize * uPixelRatio * (9.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`;

const RING_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform vec3 uEnergy;
uniform float uCharge;
varying float vBright;
void main() {
  vec2 c = abs(gl_PointCoord - 0.5);
  float box = step(c.x, 0.42) * step(c.y, 0.22);
  if (box < 0.5) discard;
  vec3 color = mix(uColor, uEnergy, uCharge) * vBright;
  gl_FragColor = vec4(color, vBright);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;

interface RingSpec {
  radius: number;
  count: number;
  tilt: [number, number];
  speed: number;
  dash: number;
  jitter: number;
  seed: number;
}

function ringGeometry(spec: RingSpec) {
  let state = spec.seed;
  const random = () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const positions: number[] = [];
  const bright: number[] = [];
  const sizes: number[] = [];
  let i = 0;
  while (i < spec.count) {
    // dashes of varying length with gaps: a broken instrument ring, not a smooth halo
    const start = random() * Math.PI * 2;
    const length = (0.04 + random() * spec.dash) * Math.PI;
    const points = Math.max(4, Math.floor(length * spec.radius * 22));
    const band = (random() - 0.5) * spec.jitter;
    for (let k = 0; k < points && i < spec.count; k++, i++) {
      const a = start + (k / points) * length;
      const r = spec.radius + band + (random() - 0.5) * spec.jitter * 0.4;
      positions.push(Math.cos(a) * r, (random() - 0.5) * spec.jitter * 0.3, Math.sin(a) * r);
      bright.push(0.35 + random() * 0.65);
      sizes.push(random() > 0.93 ? 3.2 : 1.4 + random() * 0.8);
    }
    if (random() > 0.72) {
      // a radial tick cluster
      const a = random() * Math.PI * 2;
      for (let k = 0; k < 6 && i < spec.count; k++, i++) {
        const r = spec.radius + (k - 3) * 0.035;
        positions.push(Math.cos(a) * r, 0, Math.sin(a) * r);
        bright.push(0.9);
        sizes.push(2.2);
      }
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  geometry.setAttribute("aBright", new BufferAttribute(new Float32Array(bright), 1));
  geometry.setAttribute("aSize", new BufferAttribute(new Float32Array(sizes), 1));
  return geometry;
}

const RINGS: RingSpec[] = [
  { radius: 2.05, count: 1400, tilt: [0.08, 0], speed: 0.05, dash: 0.5, jitter: 0.06, seed: 11 },
  { radius: 2.35, count: 1100, tilt: [-0.35, 0.3], speed: -0.035, dash: 0.3, jitter: 0.1, seed: 23 },
  { radius: 2.7, count: 1300, tilt: [0.2, -0.5], speed: 0.022, dash: 0.42, jitter: 0.08, seed: 37 },
  { radius: 3.05, count: 900, tilt: [1.2, 0.2], speed: -0.018, dash: 0.25, jitter: 0.12, seed: 41 },
  { radius: 3.35, count: 700, tilt: [-0.1, 0.9], speed: 0.012, dash: 0.2, jitter: 0.05, seed: 53 },
];

function Rings({ quality }: { quality: Quality }) {
  const group = useRef<Group>(null);
  const specs = quality === "low" ? RINGS.slice(0, 3) : RINGS;
  const scale = quality === "low" ? 0.5 : 1;
  const geometries = useMemo(
    () => specs.map((spec) => ringGeometry({ ...spec, count: Math.round(spec.count * scale) })),
    [specs, scale],
  );
  const uniforms = useMemo(
    () => ({
      uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
      uTime: { value: 0 },
      uColor: { value: new Color("#ffb04a").multiplyScalar(1.25) },
      uEnergy: { value: new Color("#5b9bff").multiplyScalar(1.6) },
      uCharge: { value: 0 },
    }),
    [],
  );
  useFrame((state, delta) => {
    const g = group.current;
    if (!g) return;
    const charge = Math.min(1, frame.charge * 1.4);
    g.children.forEach((child, index) => {
      const spec = specs[index];
      if (spec) child.rotation.y += delta * spec.speed * (1 + frame.charge * 3);
      const material = (child as Points).material as ShaderMaterial;
      setUniform(material, "uTime", state.clock.elapsedTime);
      setUniform(material, "uCharge", charge);
    });
  });
  return (
    <group ref={group}>
      {geometries.map((geometry, index) => {
        const spec = specs[index]!;
        return (
          <points key={spec.seed} geometry={geometry} rotation={[spec.tilt[0], 0, spec.tilt[1]]} raycast={() => null}>
            <shaderMaterial
              vertexShader={RING_VERTEX}
              fragmentShader={RING_FRAGMENT}
              uniforms={uniforms}
              transparent
              depthWrite={false}
              blending={AdditiveBlending}
            />
          </points>
        );
      })}
    </group>
  );
}

export function NexusCore({ quality, onSelect }: { quality: Quality; onSelect: () => void }) {
  const segments = quality === "low" ? 64 : quality === "medium" ? 96 : 128;
  return (
    <group>
      <OrbBody
        radius={CORE_RADIUS}
        accent="#ffab3d"
        seed={3.7}
        segments={segments}
        core
        onSelect={onSelect}
        onHover={(hovered) => {
          document.body.style.cursor = hovered ? "pointer" : "";
        }}
        drive={() => ({
          level: 0.55 + frame.levels.nexus * 0.45 + frame.coreFlash * 0.3,
          charge: frame.charge,
          flash: frame.coreFlash,
          attention: frame.attention,
        })}
      />
      <Rings quality={quality} />
    </group>
  );
}
