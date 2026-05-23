import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * M3-Q-05 — NFR-006 SLO: chat send → first MCP tool call surfaced
 * in the activity journal must complete within ≤ 800 ms at the p95.
 *
 * Measures one number: from the moment the user presses Send in the
 * chat sidebar, how long until the first `kc_*` tool call appears
 * in the activity journal's DOM. Repeats N trials (default 20),
 * takes p95.
 *
 * Gating mirrors `m0.spec.ts`'s `[full-stack only]` pattern — the
 * full local stack must be running and an accepted Claude
 * credential must be reachable. Skips with a clear reason
 * otherwise.
 *
 * Result writes to `tests/perf/results/tool_latency_<ts>.json`;
 * also attached to the Playwright report for CI artifact capture.
 */

const TRIALS = Number.parseInt(process.env.SLO_TRIALS ?? "20", 10);
const TARGET_P95_MS = 800;
const FULL_STACK = process.env.E2E_FULL_STACK === "1";

const REPO_ROOT = (() => {
  try {
    return execSync("git rev-parse --show-toplevel", {
      encoding: "utf-8",
      cwd: process.cwd(),
    }).trim();
  } catch {
    return "";
  }
})();

interface LatencyResult {
  spec: string;
  target_p95_ms: number;
  trials: number;
  samples_ms: number[];
  median_ms: number;
  p95_ms: number;
  mean_ms: number;
  ts: string;
}

async function probeAuth(): Promise<{ ok: boolean; reason: string }> {
  if (
    process.env.ANTHROPIC_API_KEY
    || process.env.CLAUDE_CODE_OAUTH_TOKEN
    || process.env.CLAUDE_CODE_USE_BEDROCK === "1"
    || process.env.CLAUDE_CODE_USE_VERTEX === "1"
  ) {
    return { ok: true, reason: "env credential present" };
  }
  if (!FULL_STACK) {
    return {
      ok: false,
      reason: "no env credential and E2E_FULL_STACK!=1 (can't probe agent)",
    };
  }
  try {
    const resp = await fetch("http://localhost:8080/api/agent/auth/status", {
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) {
      return { ok: false, reason: `agent /auth/status: ${resp.status}` };
    }
    const body = (await resp.json()) as { ok?: boolean; detail?: string };
    return {
      ok: !!body.ok,
      reason: body.ok ? "agent reports auth available" : (body.detail ?? "agent denied"),
    };
  } catch (err) {
    return {
      ok: false,
      reason: `agent unreachable: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

test.describe("M3-Q-05 NFR-006 tool-latency SLO", () => {
  test.beforeAll(async ({}, testInfo) => {
    if (!FULL_STACK) {
      testInfo.skip(
        true,
        "E2E_FULL_STACK=1 not set — start services/{server,agent,mcp,kiserver,kiconnector} first",
      );
      return;
    }
    const auth = await probeAuth();
    if (!auth.ok) testInfo.skip(true, auth.reason);
  });

  test(`p95 of chat-send → first tool-call ≤ ${TARGET_P95_MS} ms (${TRIALS} trials)`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(180_000);
    await page.goto("/");

    // Open the smallest reference project so the agent has SOME
    // context to issue `kc_*` calls against. Blinky is the M0
    // baseline — its `/health` round-trip is the cheapest realistic
    // workload.
    const picker = page.getByTestId("project-picker");
    if ((await picker.count()) > 0) {
      await picker.click();
      const option = page.getByText("blinky", { exact: false });
      if ((await option.count()) > 0) await option.click();
      await page.waitForSelector("[data-testid='pcb-canvas'][data-status='ready']", {
        timeout: 30_000,
      });
    }

    const samples: number[] = [];
    for (let i = 0; i < TRIALS; i += 1) {
      // Use a tool-call-provoking prompt that's cheap on the
      // server side. `kc_ping` is the canonical no-op tool.
      const input = page.getByTestId("chat-input");
      await input.fill("ping using kc_ping");

      const t0 = Date.now();
      await page.getByTestId("chat-send").click();
      // First tool-call card appears in the activity journal.
      await page
        .getByTestId(/activity-tool-call/)
        .first()
        .waitFor({ timeout: 30_000 });
      const dt = Date.now() - t0;
      samples.push(dt);

      // Settle so the next trial starts from idle.
      await page
        .waitForFunction(
          () =>
            !document.body.querySelector(
              "[data-testid='chat-msg-assistant'][data-streaming='true']",
            ),
          { timeout: 60_000 },
        )
        .catch(() => undefined);
      // Brief pacing pause; the activity journal de-dupes within
      // 500 ms windows so back-to-back trials would land in the
      // same span.
      await page.waitForTimeout(600);
    }

    expect(samples.length).toBe(TRIALS);
    const result = summarise(samples);
    const resultsDir = join(REPO_ROOT, "tests", "perf", "results");
    mkdirSync(resultsDir, { recursive: true });
    const fname = `tool_latency_${result.ts}.json`;
    writeFileSync(join(resultsDir, fname), JSON.stringify(result, null, 2));
    testInfo.attachments.push({
      name: "tool_latency_slo.json",
      contentType: "application/json",
      body: Buffer.from(JSON.stringify(result, null, 2)),
    });

    expect(
      result.p95_ms,
      `p95 = ${result.p95_ms.toFixed(0)} ms (target ≤ ${TARGET_P95_MS} ms). ` +
        `Full distribution: ${samples.join(", ")}`,
    ).toBeLessThanOrEqual(TARGET_P95_MS);
  });
});

function summarise(samples: number[]): LatencyResult {
  const sorted = samples.slice().sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  const p95 = sorted[Math.floor(sorted.length * 0.95)] ?? sorted[sorted.length - 1] ?? 0;
  const mean = sorted.reduce((s, v) => s + v, 0) / sorted.length;
  return {
    spec: "M3-Q-05 tool-latency SLO",
    target_p95_ms: TARGET_P95_MS,
    trials: samples.length,
    samples_ms: samples,
    median_ms: median,
    p95_ms: p95,
    mean_ms: mean,
    ts: new Date().toISOString().replace(/[:.]/g, "-"),
  };
}
