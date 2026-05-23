/**
 * `/api/ui/<tool>/<project_id>` — Hono routes for the UI-only kiclaude
 * tools (M1-P-05).
 *
 * The frontend hits these endpoints to drive direct-coordinate
 * mutations (drag-drop a symbol, draw a wire by N click-points, drop
 * a label at an exact mm position) without those tools ever appearing
 * in the Claude MCP server's tool registry — SPEC §1.4 #4.
 *
 * The gateway forwards the body to kiserver's
 * `POST /project/<id>/ui/<tool>` endpoint (where the in-memory
 * mutation runs against the registered project dict).
 */

import { Hono } from "hono";

import { kiserverOrigin } from "../proxy.js";

/** The tools the gateway accepts on `/api/ui/`. Adding a new entry
 * here AND on the kiserver side (see
 * `services/mcp/src/kc_mcp/ui_tools/__init__.py`) is the only step
 * required to expose a new UI-only mutation. */
export const ALLOWED_UI_TOOLS = [
  // M1-P-05 schematic UI tools.
  "ui_symbol_place_xy",
  "ui_wire_draw_points",
  "ui_label_place_xy",
  "ui_junction_place_xy",
  "ui_symbol_edit_props",
  // M2-P-05 PCB UI tools — added when the PCB editor tools
  // (M2-T-02..T-09) started calling them through the gateway.
  "ui_footprint_place_xy",
  "ui_footprint_move",
  "ui_track_draw_points",
  "ui_via_place_xy",
  "ui_zone_create_polygon",
  // M2-T-05 board outline tool.
  "ui_outline_create_polygon",
  // M2-T-07 net-class panel.
  "ui_netclass_set",
  "ui_netclass_delete",
  // M2-T-08 layer panel finaliser — colour picker + physical reorder.
  "ui_layer_color_set",
  "ui_layer_reorder",
] as const;
export type AllowedUiTool = (typeof ALLOWED_UI_TOOLS)[number];

function isAllowed(tool: string): tool is AllowedUiTool {
  return (ALLOWED_UI_TOOLS as readonly string[]).includes(tool);
}

/**
 * Wire `/api/ui/<tool>/<project_id>` POST handlers onto `app`.
 *
 * Each tool name maps 1:1 to the kiserver-side function. The
 * allowlist is enforced at the gateway so a misspelled tool name
 * stops at the boundary instead of reaching kiserver.
 */
export function registerUiToolRoutes(app: Hono): void {
  app.post("/api/ui/:tool/:project_id", async (c) => {
    const tool = c.req.param("tool");
    const projectId = c.req.param("project_id");
    if (!isAllowed(tool)) {
      return c.json(
        { ok: false, error: `unknown ui tool: ${tool}` },
        404,
      );
    }
    const bodyText = await c.req.text();
    let parsed: unknown;
    try {
      parsed = bodyText ? JSON.parse(bodyText) : {};
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return c.json({ ok: false, error: `invalid JSON body: ${message}` }, 400);
    }
    const args =
      parsed && typeof parsed === "object" && "args" in (parsed as Record<string, unknown>)
        ? (parsed as { args?: unknown }).args
        : parsed;

    const upstreamUrl = `${kiserverOrigin()}/project/${encodeURIComponent(
      projectId,
    )}/ui/${encodeURIComponent(tool)}`;
    let upstream: Response;
    try {
      upstream = await fetch(upstreamUrl, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args: args ?? {} }),
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return c.json(
        { ok: false, error: `kiserver unreachable: ${message}` },
        502,
      );
    }
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: upstream.headers,
    });
  });
}
