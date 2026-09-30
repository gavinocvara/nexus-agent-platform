import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import { AdditiveBlending, Color, FrontSide, type ShaderMaterial, type Sprite } from "three";

import { SUN_DIRECTION } from "./Environment";
import { CORONA_FRAGMENT, ORB_FRAGMENT, ORB_VERTEX } from "./shaders/orb";
import { glowTexture } from "./textures";
import { setUniform } from "./uniforms";

export interface OrbDrive {
  level: number;
  charge: number;
  flash: number;
  attention: number;
}

interface OrbBodyProps {
  radius: number;
  accent: string;
  seed: number;
  segments: number;
  /** Core: warmer, brighter fissures and a stronger idle corona. */
  core?: boolean;
  drive: (elapsed: number) => OrbDrive;
  onSelect?: () => void;
  onHover?: (hovered: boolean) => void;
}

const ENERGY = new Color("#3b82ff").multiplyScalar(1.4);

export function OrbBody({ radius, accent, seed, segments, core = false, drive, onSelect, onHover }: OrbBodyProps) {
  const glowRef = useRef<Sprite>(null);
  const bodyRef = useRef<ShaderMaterial>(null);
  const coronaRef = useRef<ShaderMaterial>(null);
  const accentColor = useMemo(() => new Color(accent), [accent]);
  const body = useMemo(
    () => ({
      uTime: { value: 0 },
      uLevel: { value: 0 },
      uCharge: { value: 0 },
      uFlash: { value: 0 },
      uAttention: { value: 0 },
      uSeed: { value: seed },
      uScale: { value: core ? 1.55 : 1.9 },
      uHeat: { value: core ? 1.35 : 0.9 },
      uCoverage: { value: core ? 0.62 : 0.45 },
      uBase: { value: new Color(core ? "#1c1714" : "#2a2b2e") },
      uCrackDormant: { value: core ? new Color("#ff7a1f").multiplyScalar(1.1) : new Color("#6e6c68") },
      uCrackLit: { value: core ? new Color("#ffb347").multiplyScalar(1.6) : accentColor.clone().multiplyScalar(1.7) },
      uEnergy: { value: ENERGY },
      uRim: { value: core ? new Color("#ff9b45").multiplyScalar(0.45) : accentColor.clone().multiplyScalar(0.25) },
      uSun: { value: SUN_DIRECTION },
      uSunColor: { value: new Color(core ? "#ffb070" : "#c9b7a0") },
      uAttentionColor: { value: new Color("#ffc55a") },
    }),
    [accentColor, core, seed],
  );
  const corona = useMemo(
    () => ({
      uTime: { value: 0 },
      uCharge: { value: 0 },
      uLevel: { value: 0 },
      uIdle: { value: core ? 0.5 : 0.08 },
      uIdleColor: { value: new Color(core ? "#ff9a3d" : "#6d6a64") },
      uEnergy: { value: ENERGY },
      uAccent: { value: accentColor },
    }),
    [accentColor, core],
  );
  const glowColor = useMemo(() => new Color(), []);
  const idleGlow = useMemo(() => new Color(core ? "#ff8a2a" : "#3a3936"), [core]);

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const d = drive(t);
    const b = bodyRef.current;
    setUniform(b, "uTime", t);
    setUniform(b, "uLevel", d.level);
    setUniform(b, "uCharge", d.charge);
    setUniform(b, "uFlash", d.flash);
    setUniform(b, "uAttention", d.attention);
    const c = coronaRef.current;
    setUniform(c, "uTime", t);
    setUniform(c, "uCharge", d.charge);
    setUniform(c, "uLevel", d.level);
    const sprite = glowRef.current;
    if (sprite) {
      glowColor.copy(idleGlow).lerp(accentColor, d.level).lerp(ENERGY, Math.min(1, d.charge * 1.9));
      const strength = (core ? 0.55 : 0.12) + d.level * 0.9 + d.charge * 0.75 + d.flash * 0.6;
      (sprite.material as { color: Color }).color.copy(glowColor).multiplyScalar(strength);
      const s = radius * (core ? 5.2 : 4.4) * (1 + d.charge * 0.08);
      sprite.scale.set(s, s, 1);
    }
  });

  return (
    <group>
      <mesh
        onClick={(event: ThreeEvent<MouseEvent>) => {
          event.stopPropagation();
          onSelect?.();
        }}
        onPointerOver={(event) => {
          event.stopPropagation();
          onHover?.(true);
        }}
        onPointerOut={() => onHover?.(false)}
      >
        <sphereGeometry args={[radius, segments, Math.round(segments * 0.75)]} />
        <shaderMaterial ref={bodyRef} vertexShader={ORB_VERTEX} fragmentShader={ORB_FRAGMENT} uniforms={body} />
      </mesh>
      <mesh scale={1.1} raycast={() => null}>
        <sphereGeometry args={[radius, Math.max(24, segments / 2), Math.max(16, segments / 3)]} />
        <shaderMaterial
          ref={coronaRef}
          vertexShader={ORB_VERTEX}
          fragmentShader={CORONA_FRAGMENT}
          uniforms={corona}
          transparent
          depthWrite={false}
          blending={AdditiveBlending}
          side={FrontSide}
        />
      </mesh>
      <sprite ref={glowRef} raycast={() => null} renderOrder={2}>
        <spriteMaterial
          map={glowTexture()}
          blending={AdditiveBlending}
          depthWrite={false}
          transparent
          toneMapped={false}
        />
      </sprite>
    </group>
  );
}
