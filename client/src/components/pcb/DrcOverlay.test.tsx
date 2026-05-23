import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useProjectStore, type KcirProject } from "../../stores/projectStore";
import { usePcbViewStore } from "../../stores/pcbViewStore";

import { DrcOverlay, useDrcOverlay } from "./DrcOverlay";

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

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("useDrcOverlay", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
      useProjectStore.getState().setProject(sampleProject);
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("run() POSTs to /api/connector/tools/drc and normalises the issues", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        issues: [
          {
            severity: "error",
            type: "clearance",
            layer: "F.Cu",
            position_mm: { x: 12.5, y: 7.5 },
            description: "0.05 mm gap",
            items: ["R1-1", "C2-2"],
            deficit_mm: 0.15,
          },
        ],
      }),
    );
    const cb = vi.fn();
    const { result } = renderHook(() =>
      useDrcOverlay({
        pcbPath: "/tmp/blinky.kicad_pcb",
        fetcher,
        onRunComplete: cb,
      }),
    );
    await act(async () => {
      await result.current.run();
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/connector/tools/drc",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.current.results?.ok).toBe(true);
    expect(result.current.results?.issues).toHaveLength(1);
    expect(result.current.results?.issues[0]?.position_mm).toEqual({
      x: 12.5,
      y: 7.5,
    });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("run() surfaces gateway errors", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: false, error: "kicad-cli timeout" }),
    );
    const { result } = renderHook(() =>
      useDrcOverlay({
        pcbPath: "/tmp/blinky.kicad_pcb",
        fetcher,
      }),
    );
    await act(async () => {
      await result.current.run();
    });
    expect(result.current.results?.ok).toBe(false);
    expect(result.current.error).toMatch(/kicad-cli timeout/);
  });

  it("selectIssue fires onFlyTo with the issue's mm position", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        issues: [
          {
            severity: "warning",
            type: "courtyard_overlap",
            layer: "F.Cu",
            position_mm: { x: 30, y: 40 },
            description: "overlapping courtyards",
          },
        ],
      }),
    );
    const flyTo = vi.fn();
    const { result } = renderHook(() =>
      useDrcOverlay({
        pcbPath: "/tmp/blinky.kicad_pcb",
        fetcher,
        onFlyTo: flyTo,
      }),
    );
    await act(async () => {
      await result.current.run();
    });
    act(() => result.current.selectIssue(0));
    expect(flyTo).toHaveBeenCalledWith([30, 40], "F.Cu");
    expect(result.current.selectedIndex).toBe(0);
  });

  it("refreshLive uses the wasm kernel against the current project", () => {
    const wasm = vi.fn().mockResolvedValue({
      cad: {
        checkDrc: vi.fn().mockReturnValue(
          JSON.stringify([
            {
              severity: "warning",
              kind: "clearance_violation",
              layer: "F.Cu",
              position_mm: { x: 5, y: 5 },
              description: "live finding",
              items: ["tr-1"],
              deficit_mm: 0.05,
            },
          ]),
        ),
      },
    });
    const { result } = renderHook(() =>
      useDrcOverlay({
        pcbPath: "/tmp/blinky.kicad_pcb",
        fetcher: vi.fn(),
        wasmLoader: wasm,
      }),
    );
    // First call schedules a wasm load and returns an empty marker.
    act(() => {
      result.current.refreshLive();
    });
    expect(result.current.live?.ok).toBe(false);
  });

  it("clear resets results, live, selection, and error", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, issues: [] }),
    );
    const { result } = renderHook(() =>
      useDrcOverlay({
        pcbPath: "/tmp/blinky.kicad_pcb",
        fetcher,
      }),
    );
    await act(async () => {
      await result.current.run();
    });
    expect(result.current.results).not.toBeNull();
    act(() => result.current.clear());
    expect(result.current.results).toBeNull();
    expect(result.current.live).toBeNull();
    expect(result.current.selectedIndex).toBeNull();
    expect(result.current.error).toBeNull();
  });
});

describe("DrcOverlay", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
      useProjectStore.getState().setProject(sampleProject);
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the run button, panel, and issue rows after a run", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        issues: [
          {
            severity: "error",
            type: "clearance",
            layer: "F.Cu",
            position_mm: { x: 0, y: 0 },
            description: "0.1 mm",
          },
          {
            severity: "warning",
            type: "courtyard_overlap",
            layer: "F.Cu",
            position_mm: { x: 1, y: 1 },
            description: "u1 vs u2",
          },
        ],
      }),
    );
    render(
      <DrcOverlay
        pcbPath="/tmp/blinky.kicad_pcb"
        fetcher={fetcher}
        width={400}
        height={300}
      />,
    );
    const button = screen.getByTestId("drc-run-button");
    expect(button.textContent).toBe("Run DRC");
    await act(async () => {
      fireEvent.click(button);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const rows = screen.getAllByTestId("drc-issue-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain("clearance");
    expect(rows[1]?.textContent).toContain("courtyard_overlap");
  });

  it("clicking an issue row marks it selected on the canvas marker", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        issues: [
          {
            severity: "error",
            type: "clearance",
            layer: "F.Cu",
            position_mm: { x: 0, y: 0 },
            description: "0.1 mm",
          },
        ],
      }),
    );
    const flyTo = vi.fn();
    render(
      <DrcOverlay
        pcbPath="/tmp/blinky.kicad_pcb"
        fetcher={fetcher}
        onFlyTo={flyTo}
        width={400}
        height={300}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("drc-run-button"));
    });
    fireEvent.click(screen.getByTestId("drc-issue-row"));
    expect(flyTo).toHaveBeenCalledWith([0, 0], "F.Cu");
    const cliMarkers = screen.getAllByTestId("drc-marker-cli");
    expect(cliMarkers[0]?.getAttribute("data-selected")).toBe("true");
  });

  it("hides the side panel when showPanel=false but keeps the marker overlay", () => {
    const fetcher = vi.fn();
    render(
      <DrcOverlay
        pcbPath="/tmp/blinky.kicad_pcb"
        fetcher={fetcher}
        showPanel={false}
        width={400}
        height={300}
      />,
    );
    expect(screen.queryByTestId("drc-results-panel")).toBeNull();
    expect(screen.getByTestId("drc-marker-overlay")).toBeTruthy();
  });
});
