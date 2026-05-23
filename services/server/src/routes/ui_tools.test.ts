import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";

import { createApp } from "../index.js";
import { ALLOWED_UI_TOOLS } from "./ui_tools.js";

let originalFetch: typeof fetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("/api/ui/<tool>/<project_id>", () => {
  it("rejects an unknown tool name with 404 without touching kiserver", async () => {
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    const app = createApp();
    const res = await app.request(
      "/api/ui/does_not_exist/proj-1",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args: {} }),
      },
    );
    expect(res.status).toBe(404);
    expect(fetchSpy).not.toHaveBeenCalled();
    const body = (await res.json()) as { ok: boolean; error?: string };
    expect(body.ok).toBe(false);
    expect(body.error).toContain("unknown ui tool");
  });

  it("forwards the args to kiserver for a known tool", async () => {
    const mockResponse = {
      ok: true,
      symbol_uuid: "abc-123",
      lib_id: "Device:R",
    };
    const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      expect(url).toContain("/project/proj-1/ui/ui_symbol_place_xy");
      expect(init?.method).toBe("POST");
      const bodyStr = typeof init?.body === "string" ? init.body : "";
      expect(JSON.parse(bodyStr)).toEqual({
        args: { lib_id: "Device:R", position_mm: [10, 20] },
      });
      return new Response(JSON.stringify(mockResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    const app = createApp();
    const res = await app.request("/api/ui/ui_symbol_place_xy/proj-1", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        args: { lib_id: "Device:R", position_mm: [10, 20] },
      }),
    });
    expect(res.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual(mockResponse);
  });

  it("surfaces a 502 when kiserver is unreachable", async () => {
    globalThis.fetch = (async () => {
      throw new TypeError("connect ECONNREFUSED");
    }) as unknown as typeof fetch;
    const app = createApp();
    const res = await app.request("/api/ui/ui_wire_draw_points/proj-1", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ args: { points_mm: [[0, 0], [10, 0]] } }),
    });
    expect(res.status).toBe(502);
    const body = (await res.json()) as { ok: boolean; error?: string };
    expect(body.ok).toBe(false);
    expect(body.error).toMatch(/kiserver unreachable/);
  });

  it("rejects a body that isn't valid JSON with 400", async () => {
    const app = createApp();
    const res = await app.request("/api/ui/ui_label_place_xy/proj-1", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{not-json",
    });
    expect(res.status).toBe(400);
  });

  it("exposes the expected schematic (5) + PCB (5) UI tools on the allowlist", () => {
    expect([...ALLOWED_UI_TOOLS].sort()).toEqual([
      // M1-P-05 schematic UI tools.
      "ui_junction_place_xy",
      "ui_label_place_xy",
      "ui_symbol_edit_props",
      "ui_symbol_place_xy",
      "ui_wire_draw_points",
      // M2-P-05 PCB UI tools — added by M2-T-02..T-09.
      "ui_footprint_move",
      "ui_footprint_place_xy",
      "ui_track_draw_points",
      "ui_via_place_xy",
      "ui_zone_create_polygon",
    ].sort());
  });
});
