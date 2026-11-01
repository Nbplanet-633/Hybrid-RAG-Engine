/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (`askmydocs serve`, port 8000). Proxying
// its paths keeps the browser on one origin, so dev needs no CORS setup and the
// app calls the same relative URLs it uses in production, where FastAPI serves
// the built files itself.
const API = "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    // Unit tests only; e2e/*.spec.ts belong to Playwright.
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**"],
      exclude: ["src/**/*.test.*", "src/test/**", "src/api/schema.gen.ts", "src/main.tsx"],
      // Measured at 92% statements, 81% branches, 89% functions, 94% lines.
      // Floors sit a little below; raise them as coverage rises.
      thresholds: { statements: 88, branches: 75, functions: 85, lines: 90 },
    },
    setupFiles: ["./src/test/setup.ts"],
  },
  server: {
    port: 5173,
    proxy: {
      "/library": API,
      "/healthz": API,
    },
  },
});
