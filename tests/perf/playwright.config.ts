/**
 * Playwright runner config for the M2-Q-04 NFR-003 perf benchmark.
 *
 * The e2e and a11y suites have their own configs; this one is
 * narrower — it expects the client dev server to already be
 * running (the bench needs a real GPU context, so a forked dev
 * server from the runner is fine but not required).
 *
 * Override `PERF_BASE_URL` if the dev server isn't on :5173.
 */

import { defineConfig } from "@playwright/test";

const BASE_URL = process.env.PERF_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  // The bench is wall-clock sensitive — let a slow first run finish
  // rather than retrying and conflating two GPU warmups.
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE_URL,
    trace: "off",
    video: "off",
    headless: true,
    // Force GPU acceleration when available — without it the dense
    // board runs on SwiftShader and the benchmark becomes a CPU
    // pathing exercise.
    launchOptions: {
      args: [
        "--use-gl=desktop",
        "--enable-features=Vulkan",
        "--ignore-gpu-blocklist",
      ],
    },
  },
  projects: [
    {
      name: "chromium-perf",
      use: {
        viewport: { width: 1600, height: 1000 },
      },
    },
  ],
});
