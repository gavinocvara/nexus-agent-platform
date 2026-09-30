import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The Command Center API (python -m nexus.command_center serve) owns /api; the dev server
// only proxies to it. Production is served by the same FastAPI process, same origin.
const api = process.env.NEXUS_COMMAND_CENTER_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: api, changeOrigin: false } },
  },
  build: {
    target: "es2022",
    sourcemap: false,
    // Never inline assets as data: URIs; the server CSP only allows same-origin fonts.
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1200,
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
