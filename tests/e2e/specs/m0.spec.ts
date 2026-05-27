import { expect, test } from "@playwright/test";

/**
 * M0-Q-03 — kiclaude smoke
 *
 * Plan acceptance: "Playwright opens kiclaude, sees the blinky board
 * rendered, chats 'what's the project name?', asserts Claude calls
 * kc_ping and replies in <5s."
 *
 * The UI portion (board rendered, chat input usable) runs against the
 * client dev server alone — no backend required. The backend portion
 * (Claude actually responds via kc_ping in <5s) requires the full
 * stack (services/{server,agent,mcp}) plus a valid
 * `ANTHROPIC_API_KEY`, so it is gated on both `ANTHROPIC_API_KEY` and
 * an explicit `E2E_FULL_STACK=1` opt-in flag the test author sets when
 * running the entire chain locally.
 */
test.describe("M0-Q-03 smoke", () => {
  test("blinky board renders via kicanvas", async ({ page }) => {
    const consoleErrors: string[] = [];
    // Two console errors are environment conditions in this client-only smoke,
    // not app regressions, so they are filtered out:
    //   1. The chat sidebar opens a WebSocket to the gateway at :8080, which the
    //      e2e webServer intentionally does NOT start. Browsers phrase the
    //      refusal differently — Chromium: "WebSocket connection to 'ws://…/ws'
    //      failed"; Firefox: "can't establish a connection to the server at
    //      ws://…/ws" — so match the ws://…/ws URL itself rather than the prose.
    //   2. "Unable to create WebGL2 context" on headless Firefox CI runners,
    //      which have no GPU. The structural render assertions below
    //      (pcb-canvas visible + data-status="ready" + kicanvas mounted) already
    //      prove the board rendered; the WebGL warning is non-fatal noise here.
    // Everything else still fails the test.
    const isExpectedEnvError = (s: string): boolean =>
      /ws:\/\/\S*\/ws/i.test(s) || /unable to create webgl/i.test(s);
    page.on("pageerror", (err) => {
      if (!isExpectedEnvError(err.message)) consoleErrors.push(err.message);
    });
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const text = msg.text();
      if (isExpectedEnvError(text)) return;
      consoleErrors.push(text);
    });

    await page.goto("/");

    const pcb = page.getByTestId("pcb-canvas");
    await expect(pcb).toBeVisible();
    // Bridge load + custom-element registration should land within the
    // expect timeout (5s by config).
    await expect(pcb).toHaveAttribute("data-status", "ready");

    // The kicanvas custom elements should be mounted and pointed at
    // the blinky board served by the vite middleware.
    const embed = pcb.locator("kicanvas-embed");
    await expect(embed).toHaveCount(1);
    await expect(embed).toHaveAttribute("controls", "full");

    const source = embed.locator("kicanvas-source");
    await expect(source).toHaveCount(1);
    await expect(source).toHaveAttribute(
      "src",
      "/examples/blinky/blinky.kicad_pcb",
    );

    // The blinky .kicad_pcb must be reachable over the dev-server
    // examples middleware (M0-T-04 plumbing).
    const pcbResp = await page.request.get(
      "/examples/blinky/blinky.kicad_pcb",
    );
    expect(pcbResp.status()).toBe(200);
    const pcbText = await pcbResp.text();
    expect(pcbText).toContain("(kicad_pcb");

    // The kicanvas vendored bundle must be served from /vendor/.
    const vendorResp = await page.request.get("/vendor/kicanvas.js");
    expect(vendorResp.status()).toBe(200);
    expect(vendorResp.headers()["content-type"]).toMatch(/javascript/);

    // No uncaught JS errors should fire during the load.
    expect(consoleErrors, `unexpected console errors: ${consoleErrors.join(" | ")}`).toEqual([]);
  });

  test("chat sidebar accepts a user prompt", async ({ page }) => {
    await page.goto("/");

    const sidebar = page.getByTestId("chat-sidebar");
    await expect(sidebar).toBeVisible();

    const input = page.getByTestId("chat-input");
    await expect(input).toBeVisible();
    await input.fill("what's the project name?");

    await page.getByTestId("chat-send").click();

    // The store + UI must reflect the user's prompt regardless of
    // whether a backend is connected.
    const userMessage = page.getByTestId("chat-msg-user").last();
    await expect(userMessage).toHaveText(/what's the project name\?/i);

    // The status badge should at least have tried to connect — it
    // will land on `connected` if the gateway is up or `error` /
    // `disconnected` otherwise. Both prove the WS code path ran.
    const status = page.getByTestId("chat-status");
    await expect(status).toBeVisible();
    const statusText = (await status.textContent()) ?? "";
    expect(["connecting", "connected", "disconnected", "error"]).toContain(
      statusText.trim(),
    );
  });

  test("closing then reopening the chat preserves history", async ({ page }) => {
    await page.goto("/");

    await page.getByTestId("chat-input").fill("remember me");
    await page.getByTestId("chat-send").click();
    await expect(page.getByTestId("chat-msg-user").last()).toHaveText(/remember me/);

    await page.getByTestId("chat-sidebar-close").click();
    await expect(page.getByTestId("chat-sidebar-open")).toBeVisible();
    await page.getByTestId("chat-sidebar-open").click();

    await expect(page.getByTestId("chat-msg-user").last()).toHaveText(/remember me/);
  });

  // Backend-driven assertion. Skipped unless the full local stack is
  // running AND some accepted auth path is reachable — either an
  // env credential or a `claude login` keychain entry probed via
  // `/api/agent/auth/status` (see `auth_gate.ts`).
  const fullStack = process.env.E2E_FULL_STACK === "1";
  test.describe("[full-stack only]", () => {
    test.beforeAll(async ({}, testInfo) => {
      if (!fullStack) {
        testInfo.skip(true, "needs E2E_FULL_STACK=1");
        return;
      }
      const { probeAuth } = await import("./auth_gate");
      const auth = await probeAuth({ fullStack });
      if (!auth.ok) {
        testInfo.skip(true, auth.reason);
      }
    });

    test("Claude calls kc_ping and replies in <5s", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByTestId("pcb-canvas")).toHaveAttribute(
        "data-status",
        "ready",
      );

      const started = Date.now();
      await page.getByTestId("chat-input").fill("what's the project name?");
      await page.getByTestId("chat-send").click();

      // Wait for an assistant message to appear.
      await expect(page.getByTestId("chat-msg-assistant").first()).toBeVisible({
        timeout: 5_000,
      });
      const elapsedMs = Date.now() - started;
      expect(elapsedMs, `reply took ${elapsedMs}ms`).toBeLessThan(5_000);

      // The reply should reference the kc_ping tool call somewhere in
      // the activity stream. The DOM testid for the activity log is
      // optional today, so we fall back to checking the visible body.
      const body = (await page.locator("body").innerText()).toLowerCase();
      expect(body).toContain("kc_ping");
    });
  });
});
