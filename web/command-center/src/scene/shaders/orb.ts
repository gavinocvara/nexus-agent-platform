import { SIMPLEX_3D } from "./noise";

// Obsidian body split by a fracture network (references: the fractured dark planet and the
// particle HUD orb). Fractures are Worley cell borders, warped so they read as broken rock
// rather than contours, and masked into regions so most of the surface stays dark. Dormant
// bodies are graphite; lit bodies burn in their accent; "charge" is the blue energy that
// builds before a dispatch.

export const ORB_VERTEX = /* glsl */ `
varying vec3 vObj;
varying vec3 vNormalW;
varying vec3 vViewDir;
void main() {
  vObj = position;
  vec4 world = modelMatrix * vec4(position, 1.0);
  vNormalW = normalize(mat3(modelMatrix) * normal);
  vViewDir = normalize(cameraPosition - world.xyz);
  gl_Position = projectionMatrix * viewMatrix * world;
}
`;

const WORLEY = /* glsl */ `
vec3 cellHash(vec3 p) {
  p = vec3(dot(p, vec3(127.1, 311.7, 74.7)), dot(p, vec3(269.5, 183.3, 246.1)), dot(p, vec3(113.5, 271.9, 124.6)));
  return fract(sin(p) * 43758.5453123);
}
// Distance to the border between the two nearest cells: 0 on a fracture.
float fracture(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  float d1 = 8.0;
  float d2 = 8.0;
  for (int z = -1; z <= 1; z++) {
    for (int y = -1; y <= 1; y++) {
      for (int x = -1; x <= 1; x++) {
        vec3 g = vec3(float(x), float(y), float(z));
        vec3 r = g + cellHash(i + g) - f;
        float d = dot(r, r);
        if (d < d1) { d2 = d1; d1 = d; } else if (d < d2) { d2 = d; }
      }
    }
  }
  return sqrt(d2) - sqrt(d1);
}
`;

export const ORB_FRAGMENT = /* glsl */ `
uniform float uTime;
uniform float uLevel;
uniform float uCharge;
uniform float uFlash;
uniform float uAttention;
uniform float uSeed;
uniform float uScale;
uniform float uHeat;
uniform float uCoverage;
uniform vec3 uBase;
uniform vec3 uCrackDormant;
uniform vec3 uCrackLit;
uniform vec3 uEnergy;
uniform vec3 uRim;
uniform vec3 uSun;
uniform vec3 uSunColor;
uniform vec3 uAttentionColor;
varying vec3 vObj;
varying vec3 vNormalW;
varying vec3 vViewDir;
${SIMPLEX_3D}
${WORLEY}

void main() {
  vec3 p = normalize(vObj);
  vec3 n = normalize(vNormalW);
  vec3 v = normalize(vViewDir);
  float t = uTime * 0.03;
  vec3 q = p * uScale + vec3(uSeed);

  // Jagged borders: warp the cell lookup with low-frequency noise.
  vec3 warp = vec3(snoise(q * 1.3 + t), snoise(q * 1.3 + 17.0 - t), snoise(q * 1.3 + 31.0));
  float major = fracture(q * 1.15 + warp * 0.22);
  float minor = fracture(q * 3.1 + warp * 0.35 + 9.0);
  float majorLine = 1.0 - smoothstep(0.0, 0.05, major);
  float minorLine = (1.0 - smoothstep(0.0, 0.035, minor)) * 0.55;

  // Fractures burn in regions; elsewhere they are dark seams.
  float region = smoothstep(-0.25 + (1.0 - uCoverage), 0.55, snoise(p * 1.05 + vec3(uSeed * 0.3, t * 0.6, 0.0)));
  float cracks = max(majorLine, minorLine * region) * mix(0.18, 1.0, region);

  // Heat drifts along the network so it breathes at rest.
  float heat = smoothstep(-0.4, 0.9, snoise(p * 2.1 + vec3(0.0, t * 3.0, uSeed)));
  float core = majorLine * heat * region;

  float facing = max(dot(n, v), 0.0);
  float fres = pow(1.0 - facing, 2.8);
  float sunTerm = max(dot(n, uSun), 0.0);
  float backlit = pow(1.0 - facing, 3.5) * smoothstep(-0.2, 0.6, dot(n, uSun) * 0.5 + 0.5);

  float rock = 0.55 + 0.3 * snoise(q * 6.0 + 3.0) + 0.15 * snoise(q * 14.0);
  vec3 body = uBase * rock * (0.35 + 0.75 * sunTerm + 0.25 * facing);
  body += uSunColor * backlit * 0.55;
  body += uRim * fres * (0.25 + 0.75 * uLevel);

  vec3 crackColor = mix(uCrackDormant, uCrackLit, uLevel);
  // Charging: the fissures run blue before the pulse leaves.
  float surge = clamp(uCharge * 1.7, 0.0, 1.0);
  crackColor = mix(crackColor, uEnergy * 1.25, surge);
  float gain = mix(0.45, 2.2, uLevel) * uHeat * (1.0 + surge * 0.6);
  vec3 lava = crackColor * cracks * gain * (0.45 + 1.2 * heat);
  lava += mix(crackColor, vec3(1.0, 0.9, 0.68), 0.45) * core * gain * 0.55;
  vec3 color = body + lava;

  // Blue energy rises through the fractures, then floods the rim.
  color += uEnergy * uCharge * (cracks * 2.6 + fres * 1.8 + 0.04);
  color += uCrackLit * uFlash * (fres * 1.4 + cracks * 1.3);
  color += uAttentionColor * uAttention * fres * (0.55 + 0.45 * sin(uTime * 2.4));

  gl_FragColor = vec4(color, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;

// Plasma corona: an additive shell whose flames lick outward at the silhouette.
export const CORONA_FRAGMENT = /* glsl */ `
uniform float uTime;
uniform float uCharge;
uniform float uLevel;
uniform float uIdle;
uniform vec3 uIdleColor;
uniform vec3 uEnergy;
uniform vec3 uAccent;
varying vec3 vObj;
varying vec3 vNormalW;
varying vec3 vViewDir;
${SIMPLEX_3D}

void main() {
  vec3 p = normalize(vObj);
  float facing = max(dot(normalize(vNormalW), normalize(vViewDir)), 0.0);
  float rim = pow(1.0 - facing, 3.4);
  float t = uTime * 0.22;
  float flame = snoise(p * 3.2 + vec3(0.0, -t, t * 0.5));
  flame += 0.5 * snoise(p * 7.5 + vec3(t, 0.0, -t));
  flame = smoothstep(-0.2, 1.1, flame);
  float surge = clamp(uCharge * 1.7, 0.0, 1.0);
  vec3 color = uIdleColor * uIdle * (1.0 - surge) * (0.4 + flame) * rim;
  color += uAccent * uLevel * rim * (0.6 + flame) * 1.2;
  color += uEnergy * uCharge * rim * (0.8 + 1.8 * flame) * 2.2;
  float alpha = clamp(rim * (uIdle + uLevel + uCharge * 1.6) * (0.5 + flame), 0.0, 1.0);
  gl_FragColor = vec4(color, alpha);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;
