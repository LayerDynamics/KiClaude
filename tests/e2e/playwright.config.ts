import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the M0-Q-03 smoke. The single project covers
 * Chromium; per the M0 plan, headless on CI / linux and headed on
 * macOS dev (driven by the `CI` env var).
 *
 * The `webServer` block auto-launches the Vite dev server out of
 * `client/` so the test never has to assume an externally-running
 * frontend. Backend services (services/server, services/agent, ...)
 * are NOT started here — backend-dependent assertions in `m0.spec.ts`
 * skip themselves when `ANTHROPIC_API_KEY` or `E2E_FULL_STACK` is
 * absent.
 */
const isCI = !!process.env.CI;
const headed = process.platform === "darwin" && !isCI && !process.env.E2E_HEADLESS;

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: 1,
  reporter: isCI ? [["github"], ["list"]] : "list",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5318",
    headless: !headed,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        // `pnpm --filter` would swallow the `--port` after a `--`
        // separator and Vite would land on its default port; invoke
        // Vite directly through its bin instead.
        command: "pnpm --filter client exec vite --port 5318 --strictPort",
        url: "http://localhost:5318",
        timeout: 60_000,
        reuseExistingServer: !isCI,
        stdout: "pipe",
        stderr: "pipe",
      },
});
