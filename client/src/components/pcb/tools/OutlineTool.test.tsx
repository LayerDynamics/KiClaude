import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useOutlineTool } from "./OutlineTool";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

describe("useOutlineTool", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rectangle mode: begin + end commits a CCW 4-vertex outer outline", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.beginRectangle([10, 20]));
    act(() => result.current.endRectangle([30, 40]));
    expect(result.current.outer_mm).toEqual([
      [10, 20],
      [30, 20],
      [30, 40],
      [10, 40],
    ]);
    expect(result.current.cutouts_mm).toHaveLength(0);
  });

  it("rectangle mode: zero-area drag is ignored", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.beginRectangle([5, 5]));
    act(() => result.current.endRectangle([5, 5]));
    expect(result.current.outer_mm).toHaveLength(0);
    expect(result.current.rect_anchor_mm).toBeNull();
  });

  it("polygon mode: clicks build up active ring; closeActiveRing commits as outer", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.setMode("polygon"));
    act(() => result.current.addPolygonVertex([0, 0]));
    act(() => result.current.addPolygonVertex([10, 0]));
    act(() => result.current.addPolygonVertex([5, 8]));
    expect(result.current.active_ring_mm).toHaveLength(3);
    act(() => result.current.closeActiveRing());
    expect(result.current.outer_mm).toHaveLength(3);
    expect(result.current.active_ring_mm).toHaveLength(0);
  });

  it("polygon mode: closeActiveRing with <3 vertices discards the ring", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.setMode("polygon"));
    act(() => result.current.addPolygonVertex([0, 0]));
    act(() => result.current.addPolygonVertex([10, 0]));
    act(() => result.current.closeActiveRing());
    expect(result.current.outer_mm).toHaveLength(0);
    expect(result.current.active_ring_mm).toHaveLength(0);
  });

  it("role=cutout: completed rings append to cutouts instead of replacing outer", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.beginRectangle([0, 0]));
    act(() => result.current.endRectangle([50, 50]));
    act(() => result.current.setRole("cutout"));
    act(() => result.current.beginRectangle([10, 10]));
    act(() => result.current.endRectangle([20, 20]));
    act(() => result.current.beginRectangle([30, 30]));
    act(() => result.current.endRectangle([40, 40]));
    expect(result.current.outer_mm).toHaveLength(4);
    expect(result.current.cutouts_mm).toHaveLength(2);
  });

  it("Backspace pops the last polygon vertex", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.setMode("polygon"));
    act(() => result.current.addPolygonVertex([0, 0]));
    act(() => result.current.addPolygonVertex([1, 0]));
    act(() => result.current.addPolygonVertex([1, 1]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Backspace" }));
    });
    expect(result.current.active_ring_mm).toEqual([
      [0, 0],
      [1, 0],
    ]);
  });

  it("Enter closes the active polygon ring", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.setMode("polygon"));
    act(() => result.current.addPolygonVertex([0, 0]));
    act(() => result.current.addPolygonVertex([5, 0]));
    act(() => result.current.addPolygonVertex([5, 5]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    });
    expect(result.current.outer_mm).toHaveLength(3);
  });

  it("Tab toggles role between outer and cutout", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    expect(result.current.role).toBe("outer");
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab" }));
    });
    expect(result.current.role).toBe("cutout");
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab" }));
    });
    expect(result.current.role).toBe("outer");
  });

  it("Esc cancels everything", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.beginRectangle([0, 0]));
    act(() => result.current.endRectangle([10, 10]));
    act(() => result.current.setMode("polygon"));
    act(() => result.current.addPolygonVertex([20, 20]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(result.current.outer_mm).toHaveLength(0);
    expect(result.current.active_ring_mm).toHaveLength(0);
    expect(result.current.drawing).toBe(false);
  });

  it("finish POSTs ui_outline_create_polygon with outer + cutouts", async () => {
    const fetcher = mockFetch({ ok: true, outline_uuid: "o-1" });
    const cb = vi.fn();
    const { result } = renderHook(() =>
      useOutlineTool({
        projectId: "p1",
        fetcher,
        onOutlineSaved: cb,
      }),
    );
    act(() => result.current.beginRectangle([0, 0]));
    act(() => result.current.endRectangle([100, 60]));
    act(() => result.current.setRole("cutout"));
    act(() => result.current.beginRectangle([20, 20]));
    act(() => result.current.endRectangle([30, 30]));
    await act(async () => {
      await result.current.finish();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]![0]).toMatch(/ui_outline_create_polygon/);
    const sent = JSON.parse(
      (fetcher.mock.calls[0]![1] as RequestInit).body as string,
    ).args;
    expect(sent.outline_mm).toHaveLength(4);
    expect(sent.cutouts_mm).toHaveLength(1);
    expect(sent.layer).toBe("Edge.Cuts");
    expect(cb).toHaveBeenCalledWith("o-1");
    // Successful finish clears state.
    expect(result.current.outer_mm).toHaveLength(0);
  });

  it("finish refuses without an outer outline", async () => {
    const fetcher = mockFetch({ ok: true, outline_uuid: "o-1" });
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher }),
    );
    await act(async () => {
      await result.current.finish();
    });
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.error).toMatch(/outer outline/);
  });

  it("surfaces gateway errors", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: false, error: "bad outline" }), {
        status: 400,
      }),
    );
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher }),
    );
    act(() => result.current.beginRectangle([0, 0]));
    act(() => result.current.endRectangle([10, 10]));
    await act(async () => {
      await result.current.finish();
    });
    expect(result.current.error).toMatch(/bad outline/);
  });

  it("setMode resets active ring + rect anchor", () => {
    const { result } = renderHook(() =>
      useOutlineTool({ projectId: "p1", fetcher: mockFetch({}) }),
    );
    act(() => result.current.setMode("polygon"));
    act(() => result.current.addPolygonVertex([1, 1]));
    act(() => result.current.setMode("rectangle"));
    expect(result.current.active_ring_mm).toHaveLength(0);
    act(() => result.current.beginRectangle([0, 0]));
    expect(result.current.rect_anchor_mm).toEqual([0, 0]);
    act(() => result.current.setMode("polygon"));
    expect(result.current.rect_anchor_mm).toBeNull();
  });
});
