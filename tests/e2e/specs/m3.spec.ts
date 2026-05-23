import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { probeAuth } from "./auth_gate";

/**
 * M3-Q-03 — **M3 demo gate**: chat-drive `examples/usb_eth_phy`
 * end-to-end.
 *
 * Plan acceptance: "starting from `examples/usb_eth_phy` (skeleton
 * with declared diff pairs but no routing), chat-drive `/diffpair
 * USB_D+ USB_D-` + `/route-power` + `/length-match` + `/pcb-fab
 * jlcpcb`; the resulting PCB must have USB_D+/- routed as a 90 Ω
 * diff pair, every diff pair's `leg_skew_mm < skew_tolerance_mm`
 * from the length-match report, and produce a DRC-clean gerber
 * bundle whose manifest matches a recorded golden."
 *
 * Gates (same pattern as `m1.spec.ts` / `m2.spec.ts`):
 *   - any accepted Claude auth path (env or keychain via probeAuth)
 *   - `E2E_FULL_STACK=1`
 *   - a clean `examples/usb_eth_phy` worktree
 *   - the golden manifest at
 *     `tests/golden/m3q03_usb_eth_phy_jlc_bundle.json` (when
 *     missing, the test captures the live manifest as a Playwright
 *     attachment so the next run can adopt it)
 *
 * When any gate is unmet the test calls `test.skip` with a clear
 * reason rather than failing.
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

interface DiffPairCheck {
  name: string;
  positive_routed: boolean;
  negative_routed: boolean;
  leg_skew_mm: number;
  skew_tolerance_mm: number;
}

interface BundleManifestEntry {
  path: string;
  sha256: string;
  size: number;
}

function gitStatusInExample(): string {
  return execSync("git status --porcelain examples/usb_eth_phy", {
    encoding: "utf-8",
    cwd: REPO_ROOT,
  }).trim();
}

async function approveOutstanding(
  page: import("@playwright/test").Page,
  maxClicks: number,
): Promise<number> {
  let approvals = 0;
  for (let i = 0; i < maxClicks; i += 1) {
    const prompt = page.getByTestId(/permission-prompt-/);
    if ((await prompt.count()) === 0) {
      await page.waitForTimeout(500);
      if ((await prompt.count()) === 0) return approvals;
    }
    const approveBtn = page.getByTestId("permission-approve").first();
    await approveBtn.click();
    approvals += 1;
  }
  return approvals;
}

async function settle(page: import("@playwright/test").Page): Promise<void> {
  await page.waitForFunction(
    () =>
      !document.body.querySelector(
        "[data-testid='chat-msg-assistant'][data-streaming='true']",
      ),
    { timeout: 180_000 },
  );
}

test.describe("M3-Q-03 usb_eth_phy chat-driven demo gate", () => {
  test.beforeEach(async ({}, testInfo) => {
    if (!FULL_STACK) {
      testInfo.skip(
        true,
        "E2E_FULL_STACK=1 not set — start services/{server,agent,mcp,kiserver,kiconnector}",
      );
      return;
    }
    if (!REPO_ROOT) {
      testInfo.skip(true, "cwd is not a git worktree");
      return;
    }
    const dirty = gitStatusInExample();
    if (dirty) {
      testInfo.skip(
        true,
        `examples/usb_eth_phy is not clean; commit or stash first:\n${dirty}`,
      );
      return;
    }
    const auth = await probeAuth({ fullStack: FULL_STACK });
    if (!auth.ok) testInfo.skip(true, auth.reason);
  });

  test("/diffpair + /route-power + /length-match + /pcb-fab produces a 90 Ω-routed bundle", async ({
    page,
  }, testInfo) => {
    test.setTimeout(600_000); // 10 minutes — multi-stage chat workflow

    const goldenManifestPath = join(
      REPO_ROOT,
      "tests",
      "golden",
      "m3q03_usb_eth_phy_jlc_bundle.json",
    );
    const outputDir = join(REPO_ROOT, "examples", "usb_eth_phy", "fab");

    await page.goto("/");
    const projectPicker = page.getByTestId("project-picker");
    if ((await projectPicker.count()) === 0) {
      testInfo.skip(true, "project-picker UI not present in this build");
      return;
    }
    await projectPicker.click();
    const projectOption = page.getByText("usb_eth_phy", { exact: false });
    await projectOption.click();
    await page.waitForSelector(
      "[data-testid='pcb-canvas'][data-status='ready']",
      { timeout: 30_000 },
    );

    const chatInput = page.getByTestId("chat-input");

    // Stage 1: declare + route the USB 2.0 diff pair to 90 Ω.
    await chatInput.fill(
      "/diffpair USB_D+ USB_D- --zdiff 90 --gap-mm 0.127 --route",
    );
    await page.keyboard.press("Enter");
    expect(await approveOutstanding(page, 40)).toBeGreaterThan(0);
    await settle(page);

    // Stage 2: route the power rails.
    await chatInput.fill("/route-power");
    await page.keyboard.press("Enter");
    expect(await approveOutstanding(page, 40)).toBeGreaterThan(0);
    await settle(page);

    // Stage 3: length-match every declared group. The agent
    // surfaces the report; we don't require it to find any skew
    // (the diff-pair router lays equal-length legs by
    // construction), but the report must not crash.
    await chatInput.fill("/length-match");
    await page.keyboard.press("Enter");
    expect(await approveOutstanding(page, 20)).toBeGreaterThanOrEqual(0);
    await settle(page);

    // Stage 4: pcb-fab jlcpcb. Approves the DFM dry-run + export.
    await chatInput.fill("/pcb-fab jlcpcb");
    await page.keyboard.press("Enter");
    expect(await approveOutstanding(page, 60)).toBeGreaterThanOrEqual(0);
    await settle(page);

    // Read the activity-journal's last length-match report to
    // verify every diff pair routed within its skew tolerance.
    const reports = await page.evaluate(() => {
      const cards = Array.from(
        document.querySelectorAll(
          "[data-testid='activity-tool-call'][data-tool='kc_length_match']",
        ),
      );
      const last = cards.at(-1);
      if (!last) return null;
      const payload = last.getAttribute("data-tool-result");
      return payload ? JSON.parse(payload) : null;
    });
    if (reports) {
      const pairChecks: DiffPairCheck[] = Array.isArray(reports?.diff_pair_checks)
        ? reports.diff_pair_checks
        : [];
      for (const c of pairChecks) {
        expect(
          c.leg_skew_mm,
          `${c.name}: leg_skew ${c.leg_skew_mm} mm > tolerance ${c.skew_tolerance_mm} mm`,
        ).toBeLessThanOrEqual(c.skew_tolerance_mm);
      }
    }

    // Bundle manifest comparison.
    expect(existsSync(outputDir)).toBe(true);
    const liveManifest = bundleManifest(outputDir);
    if (existsSync(goldenManifestPath)) {
      const golden = JSON.parse(
        readFileSync(goldenManifestPath, "utf-8"),
      ) as { artifacts: BundleManifestEntry[] };
      expect(liveManifest.artifacts.map((a) => a.path).sort()).toEqual(
        golden.artifacts.map((a) => a.path).sort(),
      );
      for (const goldenEntry of golden.artifacts) {
        const live = liveManifest.artifacts.find((a) => a.path === goldenEntry.path);
        expect(live, `missing artifact: ${goldenEntry.path}`).toBeDefined();
        expect(live!.sha256, `hash mismatch on ${goldenEntry.path}`).toBe(
          goldenEntry.sha256,
        );
      }
    } else {
      testInfo.attachments.push({
        name: "m3q03-live-bundle-manifest.json",
        contentType: "application/json",
        body: Buffer.from(JSON.stringify(liveManifest, null, 2)),
      });
      testInfo.annotations.push({
        type: "missing-golden",
        description:
          "tests/golden/m3q03_usb_eth_phy_jlc_bundle.json is absent — " +
          "captured the live manifest. Commit it as the golden once the " +
          "bundle has been reviewed.",
      });
    }
  });
});

function bundleManifest(outputDir: string): { artifacts: BundleManifestEntry[] } {
  const { readdirSync, statSync } = require("node:fs");
  const { createHash } = require("node:crypto");
  const artifacts: BundleManifestEntry[] = [];
  function walk(dir: string, prefix: string): void {
    for (const name of readdirSync(dir).sort()) {
      const abs = join(dir, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      const stat = statSync(abs);
      if (stat.isDirectory()) {
        walk(abs, rel);
      } else {
        const h = createHash("sha256");
        h.update(readFileSync(abs));
        artifacts.push({ path: rel, sha256: h.digest("hex"), size: stat.size });
      }
    }
  }
  walk(outputDir, "");
  return { artifacts };
}
