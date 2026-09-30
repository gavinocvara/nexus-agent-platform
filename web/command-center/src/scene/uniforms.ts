import type { ShaderMaterial } from "three";

/** R3F copies uniform entries into the material's own objects, so per-frame values must be
 * written through the material itself, never through the object passed as a prop. */
export function setUniform(material: ShaderMaterial | null | undefined, name: string, value: unknown): void {
  const uniform = material?.uniforms[name];
  if (uniform) uniform.value = value;
}
