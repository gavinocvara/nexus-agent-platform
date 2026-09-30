// Screenshot capture for visual iteration: node visual/capture.mjs <outDir> [baseUrl] [names...]
// Uses the preinstalled Chromium with SwiftShader WebGL so it runs headless anywhere.
import { chromium } from "@playwright/test";

const out = process.argv[2] ?? "shots";
const base = process.argv[3] ?? "http://127.0.0.1:8765";
const only = new Set(process.argv.slice(4));

const shots = [
  { name: "overview", size: [1440, 900], query: "" },
  { name: "owner-question", size: [1440, 900], query: "", wait: 5500 },
  { name: "nexus", size: [1440, 900], query: "?stop=1" },
  { name: "aegisops", size: [1440, 900], query: "?stop=2" },
  { name: "resident", size: [1440, 900], query: "?stop=3" },
  { name: "patchforge", size: [1440, 900], query: "?stop=4" },
  { name: "memory", size: [1440, 900], query: "?stop=6" },
  { name: "replay-dispatch", size: [1440, 900], query: "?mode=replay&episode=autonomous_draft_publication&t=8.3&paused=1&stop=0" },
  { name: "replay-owner", size: [1440, 900], query: "?mode=replay&episode=owner_approval&t=24&paused=1" },
  { name: "data", size: [1440, 900], query: "?view=data" },
  { name: "activity", size: [1440, 900], query: "?view=activity" },
  { name: "wide", size: [1920, 1080], query: "" },
  { name: "mobile", size: [390, 844], query: "" },
];

const browser = await chromium.launch({
  executablePath: process.env.PW_CHROMIUM ?? "/opt/pw-browsers/chromium",
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
for (const shot of shots) {
  if (only.size && !only.has(shot.name)) continue;
  const page = await browser.newPage({ viewport: { width: shot.size[0], height: shot.size[1] }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  const sep = shot.query ? "&" : "?";
  await page.goto(`${base}/${shot.query}${sep}capture=1`, { waitUntil: "networkidle" });
  await page.waitForTimeout(shot.wait ?? 4500);
  const keepOverlay = ["overview", "owner-question", "replay-owner", "mobile"].includes(shot.name);
  if (!keepOverlay) {
    // hide the live owner overlay for system shots so the scene is visible
    const hide = page.getByRole("button", { name: "Hide panel" });
    if (await hide.count()) await hide.first().click();
  }
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${out}/${shot.name}.png` });
  console.log(`${shot.name}: ${errors.length ? errors.join(" | ").slice(0, 400) : "ok"}`);
  await page.close();
}
await browser.close();
