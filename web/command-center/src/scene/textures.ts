import { CanvasTexture, SRGBColorSpace, type Texture } from "three";

let glow: Texture | null = null;
let spark: Texture | null = null;

/** Soft radial falloff used for every halo; generated once, 128 px. */
export function glowTexture(): Texture {
  if (glow) return glow;
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const context = canvas.getContext("2d");
  if (context) {
    const gradient = context.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    gradient.addColorStop(0, "rgba(255,255,255,1)");
    gradient.addColorStop(0.18, "rgba(255,255,255,0.55)");
    gradient.addColorStop(0.45, "rgba(255,255,255,0.14)");
    gradient.addColorStop(1, "rgba(255,255,255,0)");
    context.fillStyle = gradient;
    context.fillRect(0, 0, size, size);
  }
  glow = new CanvasTexture(canvas);
  glow.colorSpace = SRGBColorSpace;
  return glow;
}

/** A hard-cored star with a thin cross flare, for the distant sun. */
export function sparkTexture(): Texture {
  if (spark) return spark;
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const context = canvas.getContext("2d");
  if (context) {
    const c = size / 2;
    const gradient = context.createRadialGradient(c, c, 0, c, c, c);
    gradient.addColorStop(0, "rgba(255,250,235,1)");
    gradient.addColorStop(0.06, "rgba(255,236,190,0.95)");
    gradient.addColorStop(0.2, "rgba(255,190,110,0.35)");
    gradient.addColorStop(0.55, "rgba(255,150,60,0.08)");
    gradient.addColorStop(1, "rgba(255,140,40,0)");
    context.fillStyle = gradient;
    context.fillRect(0, 0, size, size);
    context.globalCompositeOperation = "lighter";
    const streak = context.createLinearGradient(0, c, size, c);
    streak.addColorStop(0, "rgba(255,200,130,0)");
    streak.addColorStop(0.5, "rgba(255,220,170,0.55)");
    streak.addColorStop(1, "rgba(255,200,130,0)");
    context.fillStyle = streak;
    context.fillRect(0, c - 1.5, size, 3);
  }
  spark = new CanvasTexture(canvas);
  spark.colorSpace = SRGBColorSpace;
  return spark;
}
