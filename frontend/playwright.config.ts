import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end smoke tests run against a real, running stack (UI + API + worker + DB).
 *   E2E_BASE_URL   default http://127.0.0.1:8080
 *   E2E_USERNAME   default admin
 *   E2E_PASSWORD   required (OBSERVENET_ADMIN_PASSWORD from .env)
 *   E2E_MODE       "demo" (default) or "real" — selects which expectations apply
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8080",
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
});
