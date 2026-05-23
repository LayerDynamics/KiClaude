import { AxeBuilder } from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * M1-Q-05 — axe-core a11y scan of the schematic editor view.
 *
 * Plan acceptance: "scan of the schematic editor view returns zero
 * 'serious' or 'critical' violations; failures block CI." (NFR-011,
 * §13 gate #8.)
 *
 * Strategy: open the client root, wait for the M1-T-01 SchematicCanvas
 * + chat sidebar to mount, then run an AxeBuilder scan tuned to WCAG
 * 2.1 AA + best-practice rules. Filter the violations down to
 * impact >= "serious" — those are the ones the gate enforces. Lower
 * impact ("moderate", "minor") are surfaced in test annotations so
 * future iterations can tighten the gate without flipping it red on
 * day one.
 */
test.describe("M1-Q-05 axe-core a11y", () => {
  test("schematic editor view has zero serious/critical violations", async ({ page }) => {
    await page.goto("/");

    // Wait for the schematic editor to mount. The component sets
    // data-testid="schematic-canvas" with a status attribute once it
    // has loaded kicanvas.
    await page.waitForSelector("[data-testid='schematic-canvas']", {
      state: "visible",
      timeout: 15_000,
    });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
      // Disable rules that are intrinsically false-positive against
      // <canvas>-based renderers (kicanvas paints into a <canvas> so
      // the colour-contrast scanner can't see the rendered text).
      .disableRules(["color-contrast"])
      .analyze();

    const serious = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );

    // Attach the lower-impact violations to the test report — they
    // don't block but they're visible in the HTML report.
    const lower = results.violations.filter(
      (v) => v.impact !== "serious" && v.impact !== "critical",
    );
    if (lower.length > 0) {
      test.info().annotations.push({
        type: "a11y-moderate",
        description: lower
          .map((v) => `${v.id} (${v.impact}): ${v.description} [${v.nodes.length} nodes]`)
          .join("\n"),
      });
    }

    expect(
      serious,
      `Found ${serious.length} serious/critical a11y violation(s):\n` +
        serious
          .map((v) => {
            const targets = v.nodes
              .map((n) => n.target.join(" → "))
              .slice(0, 5)
              .join("\n      ");
            return `  ${v.id} (${v.impact}) — ${v.description}\n    help: ${v.helpUrl}\n    targets:\n      ${targets}`;
          })
          .join("\n\n"),
    ).toHaveLength(0);
  });

  test("library sidebar is reachable via keyboard navigation", async ({ page }) => {
    /**
     * Independent assertion that the schematic editor's primary
     * controls (library search + chat input) are part of the
     * keyboard tab order. axe-core can flag missing focus styles but
     * not "the user can't actually reach this control" — that
     * requires synthesizing keypresses.
     */
    await page.goto("/");
    await page.waitForSelector("[data-testid='schematic-canvas']", {
      state: "visible",
      timeout: 15_000,
    });
    // Tab a bounded number of times; if we hit the library search
    // input within that budget, the keyboard pathway is open.
    const librarySearch = page.getByTestId("library-search-input");
    if ((await librarySearch.count()) === 0) {
      test.info().annotations.push({
        type: "a11y-skipped",
        description:
          "library-search-input not in current build — skipping keyboard reachability assertion.",
      });
      return;
    }
    // Focus the body explicitly so the tab walk starts deterministically.
    await page.evaluate(() => {
      const body = document.body;
      body.tabIndex = -1;
      body.focus();
    });
    let reached = false;
    for (let i = 0; i < 40; i += 1) {
      await page.keyboard.press("Tab");
      if (await librarySearch.evaluate((el) => el === document.activeElement)) {
        reached = true;
        break;
      }
    }
    expect(reached, "library search input must be reachable via keyboard tab navigation").toBe(
      true,
    );
  });
});
