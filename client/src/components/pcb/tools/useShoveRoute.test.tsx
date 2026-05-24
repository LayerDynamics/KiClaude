import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useProjectStore, type KcirProject } from "../../../stores/projectStore";

import { buildShoveWorld, useShoveRoute, type ShoveRouteResult } from "./useShoveRoute";

interface ShoveWasm {
  routeShove(requestJson: string): string;
}

function buildProject(): KcirProject {
  return {
    kcir_version: "0.4",
    name: "demo",
    metadata: { title: "", revision: "", company: "", date: "" },
    net_classes: [],
    pcb: {
      version: 1,
      generator: "kiclaude",
      layers: [{ id: 0, name: "F.Cu", kind: "copper" }],
      footprints: [],
      tracks: [
        {
          uuid: "track-vcc",
          net: "VCC",
          width_mm: 0.25,
          points_mm: [[0, 0.3], [10, 0.3]],
          ...({ layer: "F.Cu" } as Record<string, unknown>),
        },
      ],
      vias: [],
      zones: [],
      nets: [{ name: "VCC" }, { name: "DATA" }],
    },
  };
}

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Deterministic routeShove: parses the request, echoes a "routed"
 * plan that shoves track id=1 up to y=0.45 when the head is at y=0,
 * or "fell_back" when the request asks for a blocked lane (we encode
 * "blocked" via net === "BLOCKED"). */
function makeMockWasm(): ShoveWasm {
  return {
    routeShove: vi.fn((requestJson: string): string => {
      const req = JSON.parse(requestJson) as {
        world: { items: unknown[] };
        input: { net: string; end_mm: { x: number; y: number }; start_mm: { x: number; y: number } };
      };
      if (req.input.net === "BLOCKED") {
        return JSON.stringify({ status: "fell_back", reason: "blocked by fixed item 9" });
      }
      const result: ShoveRouteResult = {
        status: "routed",
        route_mm: [
          [req.input.start_mm.x, req.input.start_mm.y],
          [req.input.end_mm.x, req.input.end_mm.y],
        ],
        moved: [
          {
            item_id: 1,
            net: "VCC",
            layer: "F.Cu",
            width_mm: 0.25,
            points_mm: [[0, 0.45], [10, 0.45]],
          },
        ],
        shoves_applied: 1,
      };
      return JSON.stringify(result);
    }),
  };
}

const baseOpts = {
  projectId: "p1",
  layer: "F.Cu",
  net: "DATA",
  widthMm: 0.25,
  clearanceMm: 0.2,
};

describe("buildShoveWorld (M3-T-05)", () => {
  it("maps tracks on the active layer to shovable Track items + an id→uuid map", () => {
    const built = buildShoveWorld(buildProject(), "F.Cu", 0.2);
    expect(built.world.clearance_mm).toBe(0.2);
    expect(built.world.items).toHaveLength(1);
    const item = built.world.items[0] as { Track: { id: number; net: string; locked: boolean } };
    expect(item.Track.id).toBe(1);
    expect(item.Track.net).toBe("VCC");
    expect(item.Track.locked).toBe(false);
    expect(built.idToUuid[1]).toBe("track-vcc");
  });

  it("skips tracks known to be on a different layer", () => {
    const proj = buildProject();
    (proj.pcb.tracks[0] as unknown as Record<string, unknown>).layer = "B.Cu";
    const built = buildShoveWorld(proj, "F.Cu", 0.2);
    expect(built.world.items).toHaveLength(0);
  });
});

describe("useShoveRoute (M3-T-05)", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    act(() => {
      useProjectStore.getState().clear();
    });
  });

  it("produces no plan until both endpoints + wasm are set", async () => {
    const wasm = makeMockWasm();
    const { result } = renderHook(() =>
      useShoveRoute({ ...baseOpts, fetcher: vi.fn(), wasmLoader: () => Promise.resolve({ cad: wasm }) }),
    );
    // Only start set → no plan.
    act(() => result.current.setStart([0, 0]));
    await waitFor(() => expect(result.current.plan).toBeNull());
    expect(wasm.routeShove).not.toHaveBeenCalled();
  });

  it("runs routeShove when both endpoints are set and exposes the plan", async () => {
    const wasm = makeMockWasm();
    const { result } = renderHook(() =>
      useShoveRoute({ ...baseOpts, fetcher: vi.fn(), wasmLoader: () => Promise.resolve({ cad: wasm }) }),
    );
    await waitFor(() => expect((wasm.routeShove as ReturnType<typeof vi.fn>).mock).toBeDefined());
    act(() => {
      result.current.setStart([0, 0]);
      result.current.setCursor([10, 0]);
    });
    await waitFor(() => expect(result.current.plan).not.toBeNull());
    expect(result.current.plan?.shoves_applied).toBe(1);
    expect(result.current.plan?.moved[0].item_id).toBe(1);
    expect(result.current.fellBack).toBe(false);
  });

  it("flags fellBack when PnS returns fell_back", async () => {
    const wasm = makeMockWasm();
    const { result } = renderHook(() =>
      useShoveRoute({
        ...baseOpts,
        net: "BLOCKED",
        fetcher: vi.fn(),
        wasmLoader: () => Promise.resolve({ cad: wasm }),
      }),
    );
    act(() => {
      result.current.setStart([0, 0]);
      result.current.setCursor([10, 0]);
    });
    await waitFor(() => expect(result.current.fellBack).toBe(true));
    expect(result.current.fellBackReason).toContain("blocked by fixed item 9");
    expect(result.current.plan).toBeNull();
  });

  it("commit POSTs ui_shove_apply with the new track + moved tracks mapped to uuids", async () => {
    const wasm = makeMockWasm();
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, new_track_uuid: "new-1", updated_track_uuids: ["track-vcc"] }),
    );
    const { result } = renderHook(() =>
      useShoveRoute({ ...baseOpts, fetcher, wasmLoader: () => Promise.resolve({ cad: wasm }) }),
    );
    act(() => {
      result.current.setStart([0, 0]);
      result.current.setCursor([10, 0]);
    });
    await waitFor(() => expect(result.current.plan).not.toBeNull());
    let committed = false;
    await act(async () => {
      committed = await result.current.commit();
    });
    expect(committed).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/ui/ui_shove_apply/p1");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.args.new_track.net).toBe("DATA");
    expect(body.args.new_track.points_mm).toEqual([[0, 0], [10, 0]]);
    // The shoved track id=1 mapped back to its uuid track-vcc.
    expect(body.args.moved_tracks).toEqual([
      { uuid: "track-vcc", points_mm: [[0, 0.45], [10, 0.45]] },
    ]);
    // Gesture reset after a successful commit.
    await waitFor(() => expect(result.current.plan).toBeNull());
  });

  it("commit surfaces a server error and returns false", async () => {
    const wasm = makeMockWasm();
    const fetcher = vi.fn().mockResolvedValue(mockResponse({ ok: false, error: "kiserver down" }, 502));
    const { result } = renderHook(() =>
      useShoveRoute({ ...baseOpts, fetcher, wasmLoader: () => Promise.resolve({ cad: wasm }) }),
    );
    act(() => {
      result.current.setStart([0, 0]);
      result.current.setCursor([10, 0]);
    });
    await waitFor(() => expect(result.current.plan).not.toBeNull());
    let committed = true;
    await act(async () => {
      committed = await result.current.commit();
    });
    expect(committed).toBe(false);
    expect(result.current.error).toContain("kiserver down");
  });

  it("commit is a no-op when there's no plan", async () => {
    const fetcher = vi.fn();
    const { result } = renderHook(() =>
      useShoveRoute({ ...baseOpts, fetcher, wasmLoader: () => Promise.resolve({ cad: makeMockWasm() }) }),
    );
    // Let the wasm-load effect settle so we're not racing it; with no
    // drag endpoints set there is no plan, so commit short-circuits.
    await waitFor(() => expect(result.current.plan).toBeNull());
    const committed = await result.current.commit();
    expect(committed).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reset clears the in-flight gesture", async () => {
    const { result } = renderHook(() =>
      useShoveRoute({ ...baseOpts, fetcher: vi.fn(), wasmLoader: () => Promise.resolve({ cad: makeMockWasm() }) }),
    );
    act(() => {
      result.current.setStart([0, 0]);
      result.current.setCursor([10, 0]);
    });
    await waitFor(() => expect(result.current.plan).not.toBeNull());
    act(() => result.current.reset());
    expect(result.current.start).toBeNull();
    expect(result.current.cursor).toBeNull();
    expect(result.current.plan).toBeNull();
  });
});
