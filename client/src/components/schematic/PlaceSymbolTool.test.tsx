import {
  act,
  cleanup,
  createEvent,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LibrarySearchHit } from "./LibrarySidebar";
import {
  PlaceSymbolDropZone,
  usePlaceSymbolTool,
} from "./PlaceSymbolTool";

const SAMPLE_HIT: LibrarySearchHit = {
  lib_id: "Device:R",
  name: "R",
  library: "Device",
  description: "Resistor",
  footprint_filter: "R_*",
  reference: "R",
  value: "R",
  footprint: "",
  datasheet: "",
  mpn: "",
  is_power: false,
  score: 1.0,
};

afterEach(() => cleanup());

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("usePlaceSymbolTool", () => {
  it("calls /api/ui/ui_symbol_place_xy/<id> with the dropped lib_id + position", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ ok: true, symbol_uuid: "sym-1" }));
    const { result } = renderHook(() =>
      usePlaceSymbolTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    let placed: unknown;
    await act(async () => {
      placed = await result.current.place(SAMPLE_HIT, [50, 60]);
    });
    expect(placed).toMatchObject({
      symbol_uuid: "sym-1",
      lib_id: "Device:R",
      position_mm: [50, 60],
    });
    const url = String(fetcher.mock.calls[0]?.[0] ?? "");
    expect(url).toContain("/api/ui/ui_symbol_place_xy/p-1");
    const init = fetcher.mock.calls[0]?.[1] as RequestInit;
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body)) as { args: { lib_id: string; position_mm: number[] } };
    expect(body.args.lib_id).toBe("Device:R");
    expect(body.args.position_mm).toEqual([50, 60]);
  });

  it("stacks placements and pops them on undo", async () => {
    let id = 0;
    const fetcher = vi.fn(async () => {
      id += 1;
      return jsonResponse({ ok: true, symbol_uuid: `sym-${id}` });
    });
    const { result } = renderHook(() =>
      usePlaceSymbolTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    await act(async () => {
      await result.current.place(SAMPLE_HIT, [10, 10]);
      await result.current.place(SAMPLE_HIT, [20, 20]);
    });
    expect(result.current.placements.map((p) => p.symbol_uuid)).toEqual([
      "sym-1",
      "sym-2",
    ]);
    await act(async () => {
      await result.current.undo();
    });
    expect(result.current.placements.map((p) => p.symbol_uuid)).toEqual(["sym-1"]);
  });

  it("surfaces ok:false from the gateway as an error", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({ ok: false, error: "lib_id required" }, 400),
    );
    const { result } = renderHook(() =>
      usePlaceSymbolTool({
        projectId: "p-1",
        fetcher: fetcher as unknown as typeof fetch,
      }),
    );
    let threw = false;
    await act(async () => {
      try {
        await result.current.place(SAMPLE_HIT, [0, 0]);
      } catch {
        threw = true;
      }
    });
    expect(threw).toBe(true);
    expect(result.current.error).toContain("lib_id required");
  });
});

describe("PlaceSymbolDropZone", () => {
  it("decodes the symbol-hit payload and reports the pixel coordinate", () => {
    const onDrop = vi.fn();
    render(<PlaceSymbolDropZone onDrop={onDrop} />);
    const zone = screen.getByTestId("place-symbol-drop-zone");
    zone.getBoundingClientRect = () =>
      ({ left: 100, top: 50, right: 700, bottom: 500, width: 600, height: 450 }) as DOMRect;
    const dt = {
      types: ["application/x-kiclaude-lib-id"],
      getData(key: string): string {
        if (key === "application/x-kiclaude-symbol-hit") {
          return JSON.stringify(SAMPLE_HIT);
        }
        return "";
      },
    } as unknown as DataTransfer;
    // happy-dom's DragEvent constructor doesn't carry clientX/Y onto
    // the synthetic React event, so we pin them onto the event
    // instance explicitly. This mirrors what a real browser delivers
    // when a drop lands at (180, 120) on a viewport.
    const dropEvent = createEvent.drop(zone, { dataTransfer: dt });
    Object.defineProperty(dropEvent, "clientX", { value: 180 });
    Object.defineProperty(dropEvent, "clientY", { value: 120 });
    fireEvent(zone, dropEvent);
    expect(onDrop).toHaveBeenCalledWith(SAMPLE_HIT, [80, 70]);
  });

  it("ignores a drop without the symbol-hit payload", () => {
    const onDrop = vi.fn();
    render(<PlaceSymbolDropZone onDrop={onDrop} />);
    const dt = {
      types: [],
      getData() {
        return "";
      },
    } as unknown as DataTransfer;
    fireEvent.drop(screen.getByTestId("place-symbol-drop-zone"), {
      dataTransfer: dt,
    });
    expect(onDrop).not.toHaveBeenCalled();
  });
});
