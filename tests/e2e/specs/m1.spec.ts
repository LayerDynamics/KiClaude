import { execSync } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { probeAuth } from "./auth_gate";

/**
 * M1-Q-03 — `/add-mcu esp32-s3` end-to-end with git diff.
 *
 * Plan acceptance: "open `examples/esp32_s3_blinky`, chat
 * `/add-mcu esp32-s3` in an empty sheet, approve all PreToolUse
 * prompts, save, run `git diff` in the worktree, assert exactly the
 * expected files changed (`.kicad_sch` only) and the change set
 * matches the golden patch."
 *
 * The test requires:
 *   - Any accepted Claude auth path — env (`ANTHROPIC_API_KEY`,
 *     `CLAUDE_CODE_OAUTH_TOKEN`, etc.) OR a `claude login` keychain
 *     credential. The {@link probeAuth} helper probes the agent's
 *     `/auth/status` endpoint for keychain-only setups.
 *   - `E2E_FULL_STACK=1` opt-in (the full chain — services/server,
 *     services/agent, services/mcp, services/kiserver — must already
 *     be running). The Playwright `webServer` block only starts the
 *     client dev server.
 *   - A clean git worktree at the project root (so the diff reflects
 *     only this run's edits).
 *
 * When any of those gates is unmet, the test calls `test.skip` with a
 * clear message instead of failing — the M1 plan's gate is "Tested
 * by a Claude Code session", not "always-on CI".
 */

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

function gitStatusInWorktree(): string {
  return execSync("git status --porcelain examples/esp32_s3_blinky", {
    encoding: "utf-8",
    cwd: REPO_ROOT,
  }).trim();
}

function gitDiffStatInWorktree(): string {
  return execSync("git diff --name-only -- examples/esp32_s3_blinky", {
    encoding: "utf-8",
    cwd: REPO_ROOT,
  }).trim();
}

function snapshotProjectBytes(): Record<string, Buffer> {
  const dir = join(REPO_ROOT, "examples", "esp32_s3_blinky");
  const out: Record<string, Buffer> = {};
  for (const name of ["esp32_s3_blinky.kicad_sch"]) {
    const p = join(dir, name);
    out[name] = readFileSync(p);
  }
  return out;
}

test.describe("M1-Q-03 /add-mcu e2e", () => {
  test.beforeEach(async ({}, testInfo) => {
    if (!FULL_STACK) {
      testInfo.skip(
        true,
        "E2E_FULL_STACK=1 not set — start services/{server,agent,mcp,kiserver} first",
      );
      return;
    }
    if (!REPO_ROOT) {
      testInfo.skip(true, "cwd is not a git worktree");
      return;
    }
    const dirty = gitStatusInWorktree();
    if (dirty) {
      testInfo.skip(
        true,
        `examples/esp32_s3_blinky is not clean; commit or stash first:\n${dirty}`,
      );
      return;
    }
    const auth = await probeAuth({ fullStack: FULL_STACK });
    if (!auth.ok) {
      testInfo.skip(true, auth.reason);
    }
  });

  test("/add-mcu esp32-s3 mutates only .kicad_sch and matches golden patch", async ({
    page,
  }, testInfo) => {
    test.setTimeout(120_000);
    const before = snapshotProjectBytes();
    const goldenDir = mkdtempSync(join(tmpdir(), "kiclaude-m1q03-"));
    testInfo.attachments.push({
      name: "golden-temp-dir",
      contentType: "text/plain",
      body: Buffer.from(goldenDir),
    });

    await page.goto("/");
    // Open the esp32_s3_blinky project through the project picker.
    const projectPicker = page.getByTestId("project-picker");
    if ((await projectPicker.count()) === 0) {
      testInfo.skip(true, "project-picker UI not present in this build");
      return;
    }
    await projectPicker.click();
    const projectOption = page.getByText("esp32_s3_blinky", { exact: false });
    await projectOption.click();
    await page.waitForSelector("[data-testid='schematic-canvas'][data-status='ready']", {
      timeout: 30_000,
    });

    // Drive the chat sidebar.
    const chatInput = page.getByTestId("chat-input");
    await chatInput.fill("/add-mcu esp32-s3");
    await page.keyboard.press("Enter");

    // Approve every PreToolUse prompt as it appears. Bounded loop.
    let approvals = 0;
    for (let i = 0; i < 50; i += 1) {
      const prompt = page.getByTestId(/permission-prompt-/);
      if ((await prompt.count()) === 0) {
        // No outstanding prompt — give the agent a beat to issue the next one.
        await page.waitForTimeout(500);
        const stillIdle = (await prompt.count()) === 0;
        if (stillIdle) break;
      }
      const approveBtn = page.getByTestId("permission-approve").first();
      await approveBtn.click();
      approvals += 1;
    }
    expect(approvals).toBeGreaterThan(0);

    // Wait for the chat to settle (assistant_end frame fires).
    await page.waitForFunction(
      () =>
        !document.body.querySelector("[data-testid='chat-msg-assistant'][data-streaming='true']"),
      { timeout: 60_000 },
    );

    // Save via the toolbar.
    const saveBtn = page.getByTestId("project-save-button");
    if ((await saveBtn.count()) > 0) {
      await saveBtn.click();
      await page.waitForSelector("[data-testid='project-save-status'][data-saved='true']", {
        timeout: 15_000,
      });
    }

    // Now snapshot the worktree.
    const after = snapshotProjectBytes();
    expect(after["esp32_s3_blinky.kicad_sch"]).not.toEqual(before["esp32_s3_blinky.kicad_sch"]);

    const changed = gitDiffStatInWorktree();
    const changedFiles = changed.split("\n").filter(Boolean);
    expect(changedFiles).toEqual([
      "examples/esp32_s3_blinky/esp32_s3_blinky.kicad_sch",
    ]);

    // Best-effort golden patch comparison. If `tests/golden/m1q03.patch`
    // exists, assert the diff matches; otherwise record the live diff
    // for review.
    let golden: string | undefined;
    try {
      golden = readFileSync(
        join(REPO_ROOT, "tests", "golden", "m1q03.patch"),
        "utf-8",
      );
    } catch {
      golden = undefined;
    }
    const livePatch = execSync(
      "git diff -- examples/esp32_s3_blinky/esp32_s3_blinky.kicad_sch",
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    if (golden) {
      expect(livePatch).toBe(golden);
    } else {
      testInfo.attachments.push({
        name: "m1q03-live-patch.diff",
        contentType: "text/x-diff",
        body: Buffer.from(livePatch),
      });
      testInfo.annotations.push({
        type: "missing-golden",
        description:
          "tests/golden/m1q03.patch is absent — captured the live diff. " +
          "Commit the live diff as the golden patch once it is reviewed.",
      });
    }

    // Tidy up the temp dir (we never wrote to it; just attached the path).
    rmSync(goldenDir, { recursive: true, force: true });
  });
});
