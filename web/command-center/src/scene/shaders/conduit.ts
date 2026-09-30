// Energy conduits: braided fibres that sway gently and carry a travelling pulse.
// uv.x runs from the core (0) to the target system (1).

export const CONDUIT_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uPhase;
uniform vec3 uSway;
varying float vU;
varying float vV;
void main() {
  vU = uv.x;
  vV = uv.y;
  float envelope = sin(3.14159265 * uv.x);
  vec3 p = position;
  p += uSway * sin(uTime * 0.55 + uv.x * 3.6 + uPhase) * 0.16 * envelope;
  p += normal * sin(uv.x * 34.0 - uTime * 1.3 + uPhase) * 0.006;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}
`;

export const CONDUIT_FRAGMENT = /* glsl */ `
uniform float uTime;
uniform float uPhase;
uniform float uLevel;
uniform float uPulse;
uniform float uPulseStrength;
uniform float uIdle;
uniform vec3 uIdleColor;
uniform vec3 uAccent;
uniform vec3 uEnergy;
varying float vU;
varying float vV;

void main() {
  float ends = smoothstep(0.0, 0.05, vU) * (1.0 - smoothstep(0.93, 1.0, vU));
  float flow = 0.5 + 0.5 * sin(vU * 26.0 - uTime * 1.1 + uPhase);
  float grain = 0.5 + 0.5 * sin(vU * 91.0 + uPhase * 3.0 - uTime * 0.4);
  vec3 idle = uIdleColor * uIdle * (0.35 + 0.45 * flow + 0.2 * grain);
  vec3 lit = uAccent * uLevel * (0.45 + 0.55 * flow);

  float pulse = 0.0;
  float trail = 0.0;
  if (uPulse >= 0.0) {
    float d = vU - uPulse;
    pulse = exp(-(d * d) / 0.003) * uPulseStrength;
    trail = (d < 0.0 ? exp(d / 0.2) : 0.0) * 0.6 * uPulseStrength;
  }
  vec3 color = idle + lit + uEnergy * (pulse * 7.0 + trail * 1.8);
  float alpha = ends * clamp(0.38 + 0.2 * flow + 0.45 * uLevel + pulse + trail * 0.6, 0.0, 1.0);
  gl_FragColor = vec4(color, alpha);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;
