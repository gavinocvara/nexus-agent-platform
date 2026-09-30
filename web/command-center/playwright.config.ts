import { defineConfig } from "@playwright/test";

// Visual checks run against a live Command Center (python -m nexus.command_center serve).
// WebGL runs on SwiftShader so the suite works headless; frame timings there are CPU
// rasterization and say nothing about GPU performance.
const baseURL = process.env.NEXUS_COMMAND_CENTER_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  testDir: "./visual",
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      executablePath: process.env.PW_CHROMIUM ?? "/opt/pw-browsers/chromium",
      args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
    },
  },
  webServer: process.env.NEXUS_COMMAND_CENTER_URL
    ? undefined
    : {
        command: "cd ../.. && python -m nexus.command_center serve --port 8765",
        url: `${baseURL}/healthz`,
        reuseExistingServer: true,
        timeout: 60_000,
      },
});
