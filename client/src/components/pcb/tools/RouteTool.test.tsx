import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePcbViewStore } from "../../../stores/pcbViewStore";
import { useProjectStore, type KcirProject } from "../../../stores/projectStore";

import { useRouteTool } from "./RouteTool";

const sampleProject: KcirProject = {
  kcir_version: "0.3",
  name: "blinky",
  metadata: { title: "blinky", revision: "", company: "", date: "" },
  net_classes: [],
  pcb: {
    version: 1,
    generator: "kiclaude",
    layers: [
      { id: 0, name: "F.Cu", kind: "copper" },
      { id: 31, name: "B.Cu", kind: "copper" },
    ],
    footprints: [],
    tracks: [],
    vias: [],
    zones: [],
    nets: [],
  },
};

function mockFetch(responses: Array<Record<string, unknown>>) {
  let i = 0;
  return vi.fn().mockImplementation(async () => {
    const body = responses[i++] ?? { ok: true };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

function mockWasm(issues: unknown[] = []) {
  return vi.fn().mockResolvedValue({
    cad: {
      checkDrc: vi.fn().mockReturnValue(JSON.stringify(issues)),
    },
  });
}

describe("useRouteTool", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
      useProjectStore.getState().setProject(sampleProject);
      usePcbViewStore
        .getState()
        .setLayers(sampleProject.pcb.layers);
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("addCorner builds up a polyline on the active layer", () => {
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher: mockFetch([]),
        wasmLoader: mockWasm(),
      }),
    );
    act(() => result.current.addCorner([1, 1]));
    act(() => result.current.addCorner([2, 1]));
    act(() => result.current.addCorner([2, 5]));
    expect(result.current.segments).toHaveLength(1);
    expect(result.current.segments[0]?.layer).toBe("F.Cu");
    expect(result.current.segments[0]?.points_mm).toEqual([
      [1, 1],
      [2, 1],
      [2, 5],
    ]);
    expect(result.current.drawing).toBe(true);
  });

  it("dropVia opens a new segment on the opposite copper layer and switches the active layer", () => {
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher: mockFetch([]),
        wasmLoader: mockWasm(),
      }),
    );
    act(() => result.current.addCorner([0, 0]));
    act(() => result.current.addCorner([5, 0]));
    expect(usePcbViewStore.getState().activeLayerId).toBe(0); // F.Cu
    act(() => result.current.dropVia());
    expect(usePcbViewStore.getState().activeLayerId).toBe(31); // B.Cu
    expect(result.current.vias).toHaveLength(1);
    expect(result.current.vias[0]?.position_mm).toEqual([5, 0]);
    expect(result.current.segments).toHaveLength(2);
    expect(result.current.segments[1]?.layer).toBe("B.Cu");
    expect(result.current.segments[1]?.points_mm).toEqual([[5, 0]]);
  });

  it("finish POSTs one ui_track_draw_points per drawable segment and one ui_via_place_xy per via", async () => {
    // finish() POSTs all drawable tracks first, then all vias — match
    // that order in the mock response queue.
    const fetcher = mockFetch([
      { ok: true, track_uuid: "tr-1" },
      { ok: true, track_uuid: "tr-2" },
      { ok: true, via_uuid: "v-1" },
    ]);
    const trackCb = vi.fn();
    const viaCb = vi.fn();
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher,
        wasmLoader: mockWasm(),
        onTrackSaved: trackCb,
        onViaSaved: viaCb,
      }),
    );
    act(() => result.current.setNet("GND"));
    act(() => result.current.addCorner([0, 0]));
    act(() => result.current.addCorner([10, 0]));
    act(() => result.current.dropVia());
    act(() => result.current.addCorner([10, 10]));
    await act(async () => {
      await result.current.finish();
    });
    // 2 track segments + 1 via = 3 POSTs.
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls[0]![0]).toMatch(/ui_track_draw_points/);
    expect(fetcher.mock.calls[1]![0]).toMatch(/ui_track_draw_points/);
    expect(fetcher.mock.calls[2]![0]).toMatch(/ui_via_place_xy/);
    expect(trackCb).toHaveBeenCalledTimes(2);
    expect(viaCb).toHaveBeenCalledTimes(1);
    // Successful finish clears the route.
    expect(result.current.segments).toHaveLength(0);
    expect(result.current.vias).toHaveLength(0);
  });

  it("cancel clears segments + vias + cursor", () => {
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher: mockFetch([]),
        wasmLoader: mockWasm(),
      }),
    );
    act(() => result.current.addCorner([0, 0]));
    act(() => result.current.addCorner([1, 1]));
    act(() => result.current.setCursor([2, 2]));
    act(() => result.current.dropVia());
    act(() => result.current.cancel());
    expect(result.current.segments).toHaveLength(0);
    expect(result.current.vias).toHaveLength(0);
    expect(result.current.cursor_mm).toBeNull();
    expect(result.current.drawing).toBe(false);
  });

  it("Esc cancels the route", () => {
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher: mockFetch([]),
        wasmLoader: mockWasm(),
      }),
    );
    act(() => result.current.addCorner([0, 0]));
    act(() => result.current.addCorner([1, 1]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(result.current.drawing).toBe(false);
  });

  it("V drops a via while drawing, ignored when no segments exist", () => {
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher: mockFetch([]),
        wasmLoader: mockWasm(),
      }),
    );
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "v" }));
    });
    expect(result.current.vias).toHaveLength(0);
    act(() => result.current.addCorner([0, 0]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "V" }));
    });
    expect(result.current.vias).toHaveLength(1);
  });

  it("live DRC filters wasm output to issues involving the in-flight track", async () => {
    const wasmLoader = vi.fn().mockResolvedValue({
      cad: {
        checkDrc: vi.fn().mockReturnValue(
          JSON.stringify([
            {
              severity: "error",
              kind: "clearance_violation",
              position_mm: { x: 1.5, y: 0 },
              layer: "F.Cu",
              description: "0.05 mm gap",
              items: ["in-flight", "R1-1"],
              deficit_mm: 0.15,
            },
            {
              severity: "warning",
              kind: "clearance_violation",
              position_mm: { x: 99, y: 99 },
              layer: "F.Cu",
              description: "unrelated finding",
              items: ["X1", "X2"],
              deficit_mm: 0.05,
            },
          ]),
        ),
      },
    });
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher: mockFetch([]),
        wasmLoader,
      }),
    );
    // Let the wasm load promise resolve.
    await act(async () => {
      await new Promise((res) => setTimeout(res, 0));
    });
    act(() => result.current.addCorner([0, 0]));
    act(() => result.current.setCursor([3, 0]));
    expect(result.current.liveIssues).toHaveLength(1);
    expect(result.current.liveIssues[0]?.items).toContain("in-flight");
  });

  it("finish is a no-op when the route has no drawable segments", async () => {
    const fetcher = mockFetch([]);
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher,
        wasmLoader: mockWasm(),
      }),
    );
    await act(async () => {
      await result.current.finish();
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("surfaces gateway errors as `error`", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: false, error: "nope" }), {
        status: 400,
      }),
    );
    const { result } = renderHook(() =>
      useRouteTool({
        projectId: "p1",
        fetcher,
        wasmLoader: mockWasm(),
      }),
    );
    act(() => result.current.addCorner([0, 0]));
    act(() => result.current.addCorner([1, 1]));
    await act(async () => {
      await result.current.finish();
    });
    expect(result.current.error).toMatch(/nope/);
  });
});
