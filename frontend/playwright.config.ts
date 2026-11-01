import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { E2E_API_KEY } from "./e2e/constants";

// End-to-end tests drive the built web app in a real browser against a real API
// server: `askmydocs serve`, serving frontend/dist exactly as in production.
// Run `npm run build` first; `npm run e2e` does both.

const repoRoot = resolve(import.meta.dirname, "..");

/** The project's own CLI when a local virtualenv exists, else whatever is on PATH (CI). */
function askmydocsBin(): string {
  for (const candidate of [".venv/Scripts/askmydocs.exe", ".venv/bin/askmydocs"]) {
    const path = join(repoRoot, candidate);
    if (existsSync(path)) return `"${path}"`;
  }
  return "askmydocs";
}

/**
 * One API server. Each gets fresh, throwaway storage so a run never sees the
 * developer's real library or a previous run's uploads.
 */
function server(port: number, env: Record<string, string> = {}) {
  const storage = mkdtempSync(join(tmpdir(), "askmydocs-e2e-"));
  return {
    command: `${askmydocsBin()} serve --port ${port}`,
    cwd: repoRoot,
    url: `http://127.0.0.1:${port}/healthz`,
    // Always a fresh process: a reused server would carry over its library.
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      ASKMYDOCS_PROFILE: "offline",
      ASKMYDOCS_STORAGE_DIR: join(storage, "index"),
      ASKMYDOCS_UPLOADS_DIR: join(storage, "uploads"),
      ASKMYDOCS_FRONTEND_DIR: join(repoRoot, "frontend", "dist"),
      ...env,
    },
  };
}

export default defineConfig({
  testDir: "./e2e",
  // Tests share one server and its library, so they run one at a time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "app",
      testIgnore: /api-key\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:8123" },
    },
    {
      name: "api-key",
      testMatch: /api-key\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:8124" },
    },
  ],
  webServer: [server(8123), server(8124, { ASKMYDOCS_API_KEY: E2E_API_KEY })],
});
