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
