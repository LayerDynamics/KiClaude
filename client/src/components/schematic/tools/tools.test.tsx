import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useJunctionTool, JunctionToolMarkers } from "./JunctionTool";
import { useLabelTool } from "./LabelTool";
import { useWireTool, WireToolOverlay } from "./WireTool";

afterEach(() => cleanup());

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function fail(body: unknown, status = 400): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("useWireTool", () => {
  it("accumulates vertices on addPoint and persists on endWire", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => ok({ ok: true, wire_uuid: "w-1" }));
    const onWireSaved = vi.fn();
    const { result } = renderHook(() =>
      useWireTool({
        projectId: "p-1",
        sheetUuid: "sheet-1",
        fetcher: fetcher as unknown as typeof fetch,
        onWireSaved,
      }),
    );
    act(() => result.current.addPoint(0, 0));
    act(() => result.current.addPoint(10, 0));
    expect(result.current.drawing).toBe(true);
    await act(async () => {
      await result.current.endWire();
    });
    expect(onWireSaved).toHaveBeenCalledWith("w-1", [
      [0, 0],
      [10, 0],
    ]);
    expect(result.current.points).toEqual([]);
    const init = fetcher.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init?.body)) as {
      args: { points_mm: number[][] };
    };
    expect(body.args.points_mm).toEqual([
      [0, 0],
      [10, 0],
    ]);
  });

  it("drops single-vertex wires without calling the gateway", async () => {
    const fetcher = vi.fn(async () => ok({ ok: true, wire_uuid: "w" }));
    const { result } = renderHook(() =>
      useWireTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    act(() => result.current.addPoint(5, 5));
    await act(async () => {
      await result.current.endWire();
    });
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.drawing).toBe(false);
  });

  it("Esc cancels an in-flight wire", () => {
    const fetcher = vi.fn();
    const { result } = renderHook(() =>
      useWireTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    act(() => {
      result.current.addPoint(0, 0);
      result.current.addPoint(10, 0);
    });
    expect(result.current.drawing).toBe(true);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(result.current.points).toEqual([]);
    expect(result.current.drawing).toBe(false);
  });

  it("WireToolOverlay renders a path + vertex dot per point", () => {
    const fetcher = vi.fn();
    const { result } = renderHook(() =>
      useWireTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    act(() => {
      result.current.addPoint(0, 0);
      result.current.addPoint(20, 10);
    });
    render(<WireToolOverlay api={result.current} height={400} />);
    expect(screen.getByTestId("wire-tool-path").getAttribute("d")).toBe(
      "M 0 0 L 20 10",
    );
    expect(screen.getAllByTestId("wire-tool-vertex")).toHaveLength(2);
  });

  it("surfaces gateway errors via the api.error field", async () => {
    const fetcher = vi.fn(async () => fail({ ok: false, error: "denied" }));
    const { result } = renderHook(() =>
      useWireTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    act(() => {
      result.current.addPoint(0, 0);
      result.current.addPoint(5, 0);
    });
    await act(async () => {
      await result.current.endWire();
    });
    expect(result.current.error).toContain("denied");
  });
});

describe("useJunctionTool", () => {
  it("places a junction via ui_junction_place_xy", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => ok({ ok: true, junction_uuid: "j-1" }));
    const onJunctionSaved = vi.fn();
    const { result } = renderHook(() =>
      useJunctionTool({
        projectId: "p-1",
        sheetUuid: "s-1",
        fetcher: fetcher as unknown as typeof fetch,
        onJunctionSaved,
      }),
    );
    let id: string | null = null;
    await act(async () => {
      id = await result.current.placeJunction(50, 60);
    });
    expect(id).toBe("j-1");
    expect(onJunctionSaved).toHaveBeenCalledWith("j-1", [50, 60]);
    const url = String(fetcher.mock.calls[0]?.[0] ?? "");
    expect(url).toContain("/ui_junction_place_xy/p-1");
  });

  it("JunctionToolMarkers draws one marker per position", () => {
    render(
      <JunctionToolMarkers
        positions={[
          [10, 20],
          [30, 40],
        ]}
        height={300}
      />,
    );
    expect(screen.getAllByTestId("junction-tool-marker")).toHaveLength(2);
  });
});

describe("useLabelTool", () => {
  it("rejects an empty label without calling the gateway", async () => {
    const fetcher = vi.fn();
    const { result } = renderHook(() =>
      useLabelTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    let id: string | null = "unset";
    await act(async () => {
      id = await result.current.placeLabel({ x: 0, y: 0, text: "   " });
    });
    expect(id).toBeNull();
    expect(result.current.error).toBe("label text is required");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("posts the full label payload (kind + shape)", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => ok({ ok: true, label_uuid: "l-1" }));
    const { result } = renderHook(() =>
      useLabelTool({
        projectId: "p-1",
        sheetUuid: "s-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    let id: string | null = null;
    await act(async () => {
      id = await result.current.placeLabel({
        x: 1,
        y: 2,
        text: "DATA",
        kind: "hierarchical",
        shape: "input",
      });
    });
    expect(id).toBe("l-1");
    const init = fetcher.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init?.body)) as {
      args: { kind: string; shape: string; text: string };
    };
    expect(body.args.kind).toBe("hierarchical");
    expect(body.args.shape).toBe("input");
    expect(body.args.text).toBe("DATA");
  });
});
