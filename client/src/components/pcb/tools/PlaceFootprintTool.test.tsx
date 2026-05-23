import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePcbViewStore } from "../../../stores/pcbViewStore";

import {
  usePlaceFootprintTool,
  type FootprintDropPayload,
} from "./PlaceFootprintTool";

const layers = [
  { id: 0, name: "F.Cu", kind: "copper" },
  { id: 31, name: "B.Cu", kind: "copper" },
  { id: 37, name: "F.SilkS", kind: "silkscreen" },
];

const payload: FootprintDropPayload = {
  lib_id: "Resistor_SMD:R_0603_1608Metric",
  refdes: "R1",
  value: "10k",
};

function mockFetcher(body: unknown, ok = true) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: ok ? 200 : 400,
      headers: { "content-type": "application/json" },
    }),
  );
}

describe("usePlaceFootprintTool", () => {
  beforeEach(() => {
    act(() => {
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("places a footprint at the snapped grid position on the active layer", async () => {
    act(() => {
      usePcbViewStore.getState().setLayers(layers);
    });
    const fetcher = mockFetcher({
      ok: true,
      footprint_uuid: "fp-abc",
      refdes: "R1",
    });
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher }),
    );
    let placedUuid: string | undefined;
    await act(async () => {
      const record = await result.current.place(payload, [10.27, 5.13]);
      placedUuid = record?.footprint_uuid;
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const args = JSON.parse(
      (fetcher.mock.calls[0]![1] as RequestInit).body as string,
    ).args;
    // 0.5 mm snap: 10.27 → 10.5, 5.13 → 5.0.
    expect(args.position_mm).toEqual([10.5, 5]);
    expect(args.layer).toBe("F.Cu");
    expect(args.rotation_deg).toBe(0);
    expect(placedUuid).toBe("fp-abc");
    expect(result.current.placements).toHaveLength(1);
  });

  it("applies the pending rotation to the next placement and persists it across drops", async () => {
    act(() => {
      usePcbViewStore.getState().setLayers(layers);
    });
    const fetcher = mockFetcher({ ok: true, footprint_uuid: "fp-1" });
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher }),
    );
    act(() => result.current.rotate());
    act(() => result.current.rotate());
    expect(result.current.pendingRotation).toBe(180);
    await act(async () => {
      await result.current.place(payload, [0, 0]);
    });
    expect(
      JSON.parse((fetcher.mock.calls[0]![1] as RequestInit).body as string).args
        .rotation_deg,
    ).toBe(180);
    // Rotation persists for the next drop.
    await act(async () => {
      await result.current.place(payload, [0, 0]);
    });
    expect(
      JSON.parse((fetcher.mock.calls[1]![1] as RequestInit).body as string).args
        .rotation_deg,
    ).toBe(180);
  });

  it("flip toggles the active layer F.Cu ↔ B.Cu and is consumed by one drop", async () => {
    act(() => {
      usePcbViewStore.getState().setLayers(layers);
    });
    const fetcher = mockFetcher({ ok: true, footprint_uuid: "fp-1" });
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher }),
    );
    act(() => result.current.flip());
    expect(result.current.pendingFlip).toBe(true);
    await act(async () => {
      await result.current.place(payload, [0, 0]);
    });
    expect(
      JSON.parse((fetcher.mock.calls[0]![1] as RequestInit).body as string).args
        .layer,
    ).toBe("B.Cu");
    // pendingFlip cleared after consumption — next drop lands on F.Cu.
    expect(result.current.pendingFlip).toBe(false);
    await act(async () => {
      await result.current.place(payload, [0, 0]);
    });
    expect(
      JSON.parse((fetcher.mock.calls[1]![1] as RequestInit).body as string).args
        .layer,
    ).toBe("F.Cu");
  });

  it("Escape resets rotation + flip + clears error state", async () => {
    act(() => {
      usePcbViewStore.getState().setLayers(layers);
    });
    const fetcher = mockFetcher({ ok: false, error: "bad lib_id" }, false);
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher }),
    );
    act(() => result.current.rotate());
    act(() => result.current.flip());
    await act(async () => {
      await result.current.place(payload, [0, 0]);
    });
    expect(result.current.error).toBeTruthy();
    act(() => result.current.cancel());
    expect(result.current.pendingRotation).toBe(0);
    expect(result.current.pendingFlip).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("refuses to place when no layer is active", async () => {
    const fetcher = mockFetcher({ ok: true, footprint_uuid: "fp-1" });
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher }),
    );
    let placed: { footprint_uuid: string } | null = { footprint_uuid: "sentinel" };
    await act(async () => {
      placed = await result.current.place(payload, [0, 0]);
    });
    expect(placed).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.error).toMatch(/no active layer/);
  });

  it("snap respects the configurable grid step", () => {
    const fetcher = mockFetcher({});
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher, gridMm: 1.27 }),
    );
    expect(result.current.snap(0)).toBe(0);
    expect(result.current.snap(1.27)).toBe(1.27);
    expect(result.current.snap(2)).toBeCloseTo(2.54);
    // Rounds to the nearest grid step — -0.5 mm sits between -1.27
    // and 0; nearest is 0.
    expect(result.current.snap(-0.5)).toBeCloseTo(0);
    expect(result.current.snap(-1)).toBeCloseTo(-1.27);
  });

  it("gridMm=0 disables snap entirely", () => {
    const fetcher = mockFetcher({});
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher, gridMm: 0 }),
    );
    expect(result.current.snap(3.14159)).toBeCloseTo(3.14159);
  });

  it("R / F / Esc hotkeys are wired to window", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(layers);
    });
    const fetcher = mockFetcher({});
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher }),
    );
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "r" }));
    });
    expect(result.current.pendingRotation).toBe(90);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "F" }));
    });
    expect(result.current.pendingFlip).toBe(true);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(result.current.pendingRotation).toBe(0);
    expect(result.current.pendingFlip).toBe(false);
  });

  it("hotkeys are ignored while typing in an input", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(layers);
    });
    const fetcher = mockFetcher({});
    const { result } = renderHook(() =>
      usePlaceFootprintTool({ projectId: "p1", fetcher }),
    );
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    act(() => {
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "r", bubbles: true }),
      );
    });
    expect(result.current.pendingRotation).toBe(0);
    document.body.removeChild(input);
  });
});
