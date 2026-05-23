import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the M1-Q-05 a11y gate.
 *
 * Reuses the client Vite dev server (same one the M0-Q-03 e2e suite
 * uses). Headless on CI; matches the e2e suite's port (5318) so a
 * single `pnpm -F client dev` covers both gates locally.
 */
const isCI = !!process.env.CI;

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  forbidOnly: isCI,
  retries: 0,
  workers: 1,
  reporter: isCI ? [["github"], ["list"]] : "list",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: process.env.A11Y_BASE_URL ?? "http://localhost:5318",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.A11Y_BASE_URL
    ? undefined
    : {
        command: "pnpm --filter client dev --port 5318",
        url: "http://localhost:5318",
        reuseExistingServer: !isCI,
        timeout: 60_000,
        cwd: "../../",
      },
});
