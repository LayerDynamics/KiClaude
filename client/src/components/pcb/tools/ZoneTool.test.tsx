import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePcbViewStore } from "../../../stores/pcbViewStore";
import { useProjectStore, type KcirProject } from "../../../stores/projectStore";

import { useZoneTool } from "./ZoneTool";

const sampleProject: KcirProject = {
  kcir_version: "0.3",
  name: "blinky",
  metadata: { title: "blinky", revision: "", company: "", date: "" },
  net_classes: [],
  pcb: {
    version: 1,
    generator: "kiclaude",
    layers: [{ id: 0, name: "F.Cu", kind: "copper" }],
    footprints: [],
    tracks: [],
    vias: [],
    zones: [],
    nets: [],
  },
};

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

function mockWasm(returnPolys: Array<{ points: Array<{ x: number; y: number }>; holes: Array<Array<{ x: number; y: number }>> }> = []) {
  const fillZone = vi.fn().mockReturnValue(
    JSON.stringify({ polygons: returnPolys, thermal_spokes: [], warnings: [] }),
  );
  const loader = vi.fn().mockResolvedValue({ cad: { fillZone } });
  return { loader, fillZone };
}

describe("useZoneTool", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
      useProjectStore.getState().setProject(sampleProject);
      usePcbViewStore.getState().setLayers(sampleProject.pcb.layers);
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("addVertex appends to the outline", () => {
    const { loader } = mockWasm();
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher: mockFetch({ ok: true, zone_uuid: "z-1" }),
        wasmLoader: loader,
      }),
    );
    act(() => result.current.addVertex([0, 0]));
    act(() => result.current.addVertex([5, 0]));
    act(() => result.current.addVertex([5, 5]));
    expect(result.current.outline_mm).toEqual([
      [0, 0],
      [5, 0],
      [5, 5],
    ]);
    expect(result.current.drawing).toBe(true);
  });

  it("preview stays empty until the outline has at least 3 vertices", async () => {
    const { loader, fillZone } = mockWasm([
      {
        points: [
          { x: 0, y: 0 },
          { x: 5, y: 0 },
          { x: 5, y: 5 },
        ],
        holes: [],
      },
    ]);
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher: mockFetch({}),
        wasmLoader: loader,
      }),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    act(() => result.current.addVertex([0, 0]));
    expect(fillZone).not.toHaveBeenCalled();
    act(() => result.current.addVertex([5, 0]));
    expect(fillZone).not.toHaveBeenCalled();
    act(() => result.current.addVertex([5, 5]));
    expect(fillZone).toHaveBeenCalled();
    expect(result.current.preview.polygons).toHaveLength(1);
  });

  it("finish POSTs ui_zone_create_polygon with the outline + clearance", async () => {
    const fetcher = mockFetch({ ok: true, zone_uuid: "z-1" });
    const { loader } = mockWasm();
    const savedCb = vi.fn();
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher,
        wasmLoader: loader,
        onZoneSaved: savedCb,
      }),
    );
    act(() => result.current.setNet("GND"));
    act(() => result.current.setClearance(0.3));
    act(() => result.current.addVertex([0, 0]));
    act(() => result.current.addVertex([10, 0]));
    act(() => result.current.addVertex([10, 10]));
    act(() => result.current.addVertex([0, 10]));
    await act(async () => {
      await result.current.finish();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]![0]).toMatch(/ui_zone_create_polygon/);
    const sent = JSON.parse(
      (fetcher.mock.calls[0]![1] as RequestInit).body as string,
    ).args;
    expect(sent.net).toBe("GND");
    expect(sent.layer).toBe("F.Cu");
    expect(sent.clearance_mm).toBe(0.3);
    expect(sent.outline_mm).toHaveLength(4);
    expect(savedCb).toHaveBeenCalledWith("z-1");
    // Successful finish clears the outline.
    expect(result.current.outline_mm).toHaveLength(0);
  });

  it("finish with fewer than 3 vertices is a no-op (no POST, cancels state)", async () => {
    const fetcher = mockFetch({});
    const { loader } = mockWasm();
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher,
        wasmLoader: loader,
      }),
    );
    act(() => result.current.addVertex([0, 0]));
    act(() => result.current.addVertex([5, 0]));
    await act(async () => {
      await result.current.finish();
    });
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.outline_mm).toHaveLength(0);
  });

  it("Esc cancels the in-flight outline", () => {
    const { loader } = mockWasm();
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher: mockFetch({}),
        wasmLoader: loader,
      }),
    );
    act(() => result.current.addVertex([0, 0]));
    act(() => result.current.addVertex([1, 1]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(result.current.drawing).toBe(false);
  });

  it("Backspace removes the most recent vertex", () => {
    const { loader } = mockWasm();
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher: mockFetch({}),
        wasmLoader: loader,
      }),
    );
    act(() => result.current.addVertex([0, 0]));
    act(() => result.current.addVertex([1, 0]));
    act(() => result.current.addVertex([1, 1]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Backspace" }));
    });
    expect(result.current.outline_mm).toEqual([
      [0, 0],
      [1, 0],
    ]);
  });

  it("surfaces gateway errors as `error`", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: false, error: "bad outline" }), {
        status: 400,
      }),
    );
    const { loader } = mockWasm();
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher,
        wasmLoader: loader,
      }),
    );
    act(() => result.current.addVertex([0, 0]));
    act(() => result.current.addVertex([1, 0]));
    act(() => result.current.addVertex([1, 1]));
    await act(async () => {
      await result.current.finish();
    });
    expect(result.current.error).toMatch(/bad outline/);
  });

  it("setCursor refreshes the preview with the cursor point as a closing edge", async () => {
    const { loader, fillZone } = mockWasm([
      {
        points: [
          { x: 0, y: 0 },
          { x: 5, y: 0 },
          { x: 5, y: 5 },
        ],
        holes: [],
      },
    ]);
    const { result } = renderHook(() =>
      useZoneTool({
        projectId: "p1",
        fetcher: mockFetch({}),
        wasmLoader: loader,
      }),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    act(() => result.current.addVertex([0, 0]));
    act(() => result.current.addVertex([5, 0]));
    expect(fillZone).not.toHaveBeenCalled();
    act(() => result.current.setCursor([5, 5]));
    expect(fillZone).toHaveBeenCalled();
    // The most recent fillZone call should include the cursor point
    // as the third outline vertex (closing the triangle).
    const lastCallInput = JSON.parse(
      fillZone.mock.calls[fillZone.mock.calls.length - 1]![0] as string,
    );
    expect(lastCallInput.outline.points).toHaveLength(3);
    expect(lastCallInput.outline.points[2]).toEqual({ x: 5, y: 5 });
  });
});
