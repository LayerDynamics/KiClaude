/**
 * `/api/approval/*` — Hono routes for the M1-P-06 permission gate.
 *
 * When the agent service's PreToolUse permission hook needs a UI
 * decision on a mutating call, it POSTs to `/api/approval/request`.
 * The gateway pushes the prompt to the connected client over the
 * existing kiclaude WebSocket (M0-T-03) and waits up to
 * `APPROVAL_TIMEOUT_MS` for the user's reply. If no client is
 * connected or the timeout expires, the request resolves to `"deny"`
 * — kiclaude refuses to silently mutate state.
 *
 * The dispatcher is exposed as a separate object so unit tests can
 * provide a deterministic decision without standing up a real WS
 * client.
 */

import { Hono } from "hono";

export const APPROVAL_TIMEOUT_MS = 60_000;

/** One pending approval prompt. */
export interface ApprovalPrompt {
  id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  session_id: string;
  project_id: string | null;
}

export type ApprovalDecision = "allow" | "deny" | "ask";

/** Pluggable transport — the WS-backed implementation pushes prompts
 * to the connected client and resolves on the reply; tests inject a
 * synchronous stub. */
export interface ApprovalDispatcher {
  request(prompt: ApprovalPrompt, signal: AbortSignal): Promise<ApprovalDecision>;
}

/** Module-level dispatcher; tests replace it via `setDispatcher`. */
let activeDispatcher: ApprovalDispatcher = {
  // Default until M1-T-07 wires the real WS bridge. Always denies so
  // we never quietly approve a mutation when no UI is present.
  async request(_prompt, _signal) {
    return "deny";
  },
};

export function setDispatcher(dispatcher: ApprovalDispatcher | null): void {
  activeDispatcher = dispatcher ?? {
    async request() {
      return "deny";
    },
  };
}

export function getDispatcher(): ApprovalDispatcher {
  return activeDispatcher;
}

interface ApprovalRequestBody {
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  session_id?: string;
  project_id?: string | null;
  timeout_ms?: number;
}

/** Mount `/api/approval/request` on `app`. */
export function registerApprovalRoutes(app: Hono): void {
  app.post("/api/approval/request", async (c) => {
    let body: ApprovalRequestBody = {};
    try {
      const text = await c.req.text();
      body = text ? (JSON.parse(text) as ApprovalRequestBody) : {};
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return c.json({ ok: false, error: `invalid JSON body: ${message}` }, 400);
    }
    if (!body.tool_name) {
      return c.json({ ok: false, error: "tool_name is required" }, 400);
    }
    const promptId = crypto.randomUUID();
    const prompt: ApprovalPrompt = {
      id: promptId,
      tool_name: body.tool_name,
      tool_input: body.tool_input ?? {},
      session_id: body.session_id ?? "",
      project_id: body.project_id ?? null,
    };
    const timeoutMs =
      typeof body.timeout_ms === "number" && body.timeout_ms > 0
        ? Math.min(body.timeout_ms, 5 * APPROVAL_TIMEOUT_MS)
        : APPROVAL_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const decision = await activeDispatcher.request(prompt, controller.signal);
      return c.json({
        ok: true,
        id: promptId,
        decision,
      });
    } catch (err) {
      if (controller.signal.aborted) {
        return c.json(
          { ok: true, id: promptId, decision: "deny", error: "timeout" },
          200,
        );
      }
      const message = err instanceof Error ? err.message : String(err);
      return c.json(
        { ok: false, id: promptId, decision: "deny", error: message },
        500,
      );
    } finally {
      clearTimeout(timer);
    }
  });
}
