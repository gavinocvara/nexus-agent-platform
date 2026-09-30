import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import {
  AdditiveBlending,
  BackSide,
  BufferAttribute,
  BufferGeometry,
  Color,
  IcosahedronGeometry,
  InstancedMesh,
  Matrix4,
  Object3D,
  Quaternion,
  Vector3,
  type Points,
  type ShaderMaterial,
} from "three";

import type { Quality } from "../state/store";
import { SIMPLEX_3D } from "./shaders/noise";
import { sparkTexture } from "./textures";
import { setUniform } from "./uniforms";

export const SUN_DIRECTION = new Vector3(-0.78, 0.2, -0.6).normalize();

function seeded(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const BACKDROP_FRAGMENT = /* glsl */ `
uniform vec3 uSun;
varying vec3 vDir;
${SIMPLEX_3D}
void main() {
  vec3 d = normalize(vDir);
  float sun = max(dot(d, uSun), 0.0);
  vec3 color = vec3(0.012, 0.014, 0.02);
  color += vec3(1.0, 0.55, 0.18) * pow(sun, 7.0) * 0.32;
  color += vec3(1.0, 0.72, 0.36) * pow(sun, 60.0) * 0.6;
  // a cold, faint nebula opposite the sun
  float cold = max(dot(d, -uSun), 0.0);
  float n = snoise(d * 2.2) * 0.5 + snoise(d * 5.3 + 3.0) * 0.25;
  color += vec3(0.08, 0.1, 0.32) * smoothstep(0.1, 0.9, n + 0.35) * pow(cold, 1.6) * 0.45;
  color += vec3(0.25, 0.12, 0.35) * smoothstep(0.4, 1.0, n) * pow(cold, 3.0) * 0.18;
  gl_FragColor = vec4(color, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;

const BACKDROP_VERTEX = /* glsl */ `
varying vec3 vDir;
void main() {
  vDir = position;
  vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  gl_Position = p.xyww;
}
`;

function Backdrop() {
  const uniforms = useMemo(() => ({ uSun: { value: SUN_DIRECTION } }), []);
  return (
    <mesh renderOrder={-10} frustumCulled={false}>
      <sphereGeometry args={[400, 48, 24]} />
      <shaderMaterial
        side={BackSide}
        depthWrite={false}
        vertexShader={BACKDROP_VERTEX}
        fragmentShader={BACKDROP_FRAGMENT}
        uniforms={uniforms}
      />
    </mesh>
  );
}

const STAR_VERTEX = /* glsl */ `
attribute float aSize;
attribute float aPhase;
uniform float uTime;
uniform float uPixelRatio;
varying float vTwinkle;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vTwinkle = 0.65 + 0.35 * sin(uTime * (0.6 + aPhase) + aPhase * 40.0);
  gl_PointSize = aSize * uPixelRatio;
  gl_Position = projectionMatrix * mv;
}
`;

const STAR_FRAGMENT = /* glsl */ `
varying float vTwinkle;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  float a = smoothstep(0.5, 0.0, d);
  gl_FragColor = vec4(vec3(0.9, 0.92, 1.0) * vTwinkle, a * vTwinkle);
}
`;

function Stars({ count }: { count: number }) {
  const ref = useRef<Points>(null);
  const geometry = useMemo(() => {
    const random = seeded(7);
    const positions = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    const phases = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const u = random() * 2 - 1;
      const theta = random() * Math.PI * 2;
      const r = 150 + random() * 150;
      const s = Math.sqrt(1 - u * u);
      positions.set([r * s * Math.cos(theta), r * u, r * s * Math.sin(theta)], i * 3);
      const bright = random();
      sizes[i] = bright > 0.985 ? 3.2 : bright > 0.9 ? 2.0 : 1.1;
      phases[i] = random();
    }
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(positions, 3));
    g.setAttribute("aSize", new BufferAttribute(sizes, 1));
    g.setAttribute("aPhase", new BufferAttribute(phases, 1));
    return g;
  }, [count]);
  const uniforms = useMemo(
    () => ({ uTime: { value: 0 }, uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) } }),
    [],
  );
  useFrame((state) => {
    setUniform(ref.current?.material as ShaderMaterial | undefined, "uTime", state.clock.elapsedTime);
  });
  return (
    <points ref={ref} geometry={geometry} frustumCulled={false}>
      <shaderMaterial
        vertexShader={STAR_VERTEX}
        fragmentShader={STAR_FRAGMENT}
        uniforms={uniforms}
        transparent
        depthWrite={false}
        blending={AdditiveBlending}
      />
    </points>
  );
}

function Sun() {
  const position = useMemo(() => SUN_DIRECTION.clone().multiplyScalar(140), []);
  return (
    <group>
      <sprite position={position} scale={[46, 46, 1]} renderOrder={-5}>
        <spriteMaterial
          map={sparkTexture()}
          color={new Color(1.6, 1.2, 0.8)}
          blending={AdditiveBlending}
          depthWrite={false}
          transparent
          toneMapped={false}
        />
      </sprite>
      <directionalLight position={position} intensity={2.6} color="#ffd9a8" />
    </group>
  );
}

/** Rim-lit rock silhouettes, instanced: one draw call. */
function Debris({ count }: { count: number }) {
  const ref = useRef<InstancedMesh>(null);
  const data = useMemo(() => {
    const random = seeded(19);
    const items: Array<{ position: Vector3; scale: Vector3; axis: Vector3; speed: number; spin: number }> = [];
    for (let i = 0; i < count; i++) {
      const belt = random() < 0.78;
      const angle = random() * Math.PI * 2;
      const radius = belt ? 11 + random() * 9 : 4.5 + random() * 22;
      const y = belt ? (random() - 0.5) * 3.2 : (random() - 0.5) * 14;
      const s = belt ? 0.05 + Math.pow(random(), 3) * 0.55 : 0.04 + random() * 0.18;
      items.push({
        position: new Vector3(Math.cos(angle) * radius, y, Math.sin(angle) * radius),
        scale: new Vector3(s * (0.7 + random() * 0.6), s * (0.6 + random() * 0.5), s),
        axis: new Vector3(random() - 0.5, random() - 0.5, random() - 0.5).normalize(),
        speed: 0.004 + random() * 0.01,
        spin: random() * Math.PI * 2,
      });
    }
    return items;
  }, [count]);
  const geometry = useMemo(() => {
    const g = new IcosahedronGeometry(1, 1);
    const random = seeded(3);
    const position = g.getAttribute("position");
    const v = new Vector3();
    for (let i = 0; i < position.count; i++) {
      v.fromBufferAttribute(position, i).multiplyScalar(0.72 + random() * 0.5);
      position.setXYZ(i, v.x, v.y, v.z);
    }
    g.computeVertexNormals();
    return g;
  }, []);
  const scratch = useMemo(
    () => ({ object: new Object3D(), q: new Quaternion(), m: new Matrix4(), up: new Vector3(0, 1, 0) }),
    [],
  );
  useFrame((state) => {
    const mesh = ref.current;
    if (!mesh) return;
    const t = state.clock.elapsedTime;
    data.forEach((item, i) => {
      const o = scratch.object;
      o.position.copy(item.position).applyAxisAngle(scratch.up, t * item.speed);
      o.quaternion.setFromAxisAngle(item.axis, item.spin + t * item.speed * 12);
      o.scale.copy(item.scale);
      o.updateMatrix();
      mesh.setMatrixAt(i, o.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  });
  return (
    <instancedMesh ref={ref} args={[geometry, undefined, count]} frustumCulled={false}>
      <meshStandardMaterial color="#26221f" roughness={0.92} metalness={0.05} flatShading />
    </instancedMesh>
  );
}

export function Environment({ quality }: { quality: Quality }) {
  const stars = quality === "low" ? 900 : quality === "medium" ? 1600 : 2600;
  const debris = quality === "low" ? 70 : quality === "medium" ? 150 : 240;
  return (
    <>
      <color attach="background" args={["#040507"]} />
      <Backdrop />
      <Stars count={stars} />
      <Sun />
      <ambientLight intensity={0.06} color="#8aa0ff" />
      <pointLight position={[0, 0, 0]} intensity={14} distance={16} decay={1.6} color="#ff9a3d" />
      <Debris count={debris} />
    </>
  );
}
