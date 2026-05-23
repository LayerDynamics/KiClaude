import { cleanup, render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePcbViewStore } from "../../stores/pcbViewStore";

import { LayerStack } from "./LayerStack";

const sampleLayers = [
  { id: 0, name: "F.Cu", kind: "copper" },
  { id: 31, name: "B.Cu", kind: "copper" },
  { id: 37, name: "F.SilkS", kind: "silkscreen" },
  { id: 44, name: "Edge.Cuts", kind: "outline" },
];

describe("LayerStack", () => {
  beforeEach(() => {
    act(() => {
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
    });
  });
  afterEach(() => cleanup());

  it("shows an empty state when no layers are loaded", () => {
    render(<LayerStack />);
    expect(screen.getByTestId("layer-stack").dataset.status).toBe("empty");
  });

  it("renders one row per layer, each carrying a stable data-layer-id", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
    });
    render(<LayerStack />);
    const rows = screen.getAllByTestId("layer-row");
    expect(rows).toHaveLength(4);
    expect(rows.map((r) => r.dataset.layerId)).toEqual(["0", "31", "37", "44"]);
  });

  it("marks the active layer with data-active=true", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
    });
    render(<LayerStack />);
    const active = screen.getAllByTestId("layer-row").find(
      (r) => r.dataset.active === "true",
    );
    expect(active).toBeTruthy();
    expect(active?.dataset.layerId).toBe("0");
  });

  it("clicking a layer name promotes it to active", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
    });
    render(<LayerStack />);
    const target = screen.getAllByTestId("layer-row")[2];
    const trigger = target?.querySelector("button");
    expect(trigger).toBeTruthy();
    fireEvent.click(trigger!);
    expect(usePcbViewStore.getState().activeLayerId).toBe(37);
  });

  it("toggling the visibility checkbox flips layerView.visible", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
    });
    render(<LayerStack />);
    const fcuVisibility = screen
      .getAllByTestId("layer-row")[0]
      ?.querySelector("[data-testid='layer-visibility']") as HTMLInputElement;
    expect(fcuVisibility.checked).toBe(true);
    fireEvent.click(fcuVisibility);
    expect(usePcbViewStore.getState().layerView[0]?.visible).toBe(false);
    fireEvent.click(fcuVisibility);
    expect(usePcbViewStore.getState().layerView[0]?.visible).toBe(true);
  });

  it("dragging the opacity slider updates layerView.opacity (clamped 0..1)", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
    });
    render(<LayerStack />);
    const slider = screen
      .getAllByTestId("layer-row")[1]
      ?.querySelector("[data-testid='layer-opacity']") as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "45" } });
    expect(usePcbViewStore.getState().layerView[31]?.opacity).toBeCloseTo(0.45);
    fireEvent.change(slider, { target: { value: "0" } });
    expect(usePcbViewStore.getState().layerView[31]?.opacity).toBe(0);
    fireEvent.change(slider, { target: { value: "100" } });
    expect(usePcbViewStore.getState().layerView[31]?.opacity).toBe(1);
  });

  it("drag-and-drop reorders adjacent layers of the same kind", () => {
    // Two inner copper layers give us a legal swap target that
    // doesn't trip the F.Cu / B.Cu stackup anchor or the cross-kind
    // physical-limit guard.
    const withInners = [
      { id: 0, name: "F.Cu", kind: "copper" },
      { id: 1, name: "In1.Cu", kind: "copper" },
      { id: 2, name: "In2.Cu", kind: "copper" },
      { id: 31, name: "B.Cu", kind: "copper" },
    ];
    act(() => {
      usePcbViewStore.getState().setLayers(withInners);
    });
    render(<LayerStack />);
    const rows = screen.getAllByTestId("layer-row");
    const in1 = rows[1]!;
    const in2 = rows[2]!;
    const data = new Map<string, string>();
    const dt = {
      effectAllowed: "move",
      dropEffect: "move",
      types: ["application/x-kiclaude-layer-id"],
      setData(format: string, value: string) {
        data.set(format, value);
      },
      getData(format: string) {
        return data.get(format) ?? "";
      },
    };
    fireEvent.dragStart(in1, { dataTransfer: dt });
    fireEvent.dragOver(in2, { dataTransfer: dt });
    fireEvent.drop(in2, { dataTransfer: dt });
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([0, 2, 1, 31]);
  });

  it("drag-and-drop refuses to move F.Cu off the stackup top", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
    });
    render(<LayerStack />);
    const rows = screen.getAllByTestId("layer-row");
    const fcu = rows[0]!;
    const silk = rows[2]!;
    const data = new Map<string, string>();
    const dt = {
      effectAllowed: "move",
      dropEffect: "move",
      types: ["application/x-kiclaude-layer-id"],
      setData(format: string, value: string) {
        data.set(format, value);
      },
      getData(format: string) {
        return data.get(format) ?? "";
      },
    };
    fireEvent.dragStart(fcu, { dataTransfer: dt });
    fireEvent.dragOver(silk, { dataTransfer: dt });
    fireEvent.drop(silk, { dataTransfer: dt });
    // Order unchanged — F.Cu is fixed.
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([0, 31, 37, 44]);
  });

  it("colour picker change updates the store and POSTs ui_layer_color_set when a project is loaded", async () => {
    const { useProjectStore } = await import("../../stores/projectStore");
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
      useProjectStore.getState().setProject(
        {
          kcir_version: "0.3",
          name: "blinky",
          metadata: { title: "blinky", revision: "", company: "", date: "" },
          net_classes: [],
          pcb: {
            version: 1,
            generator: "kiclaude",
            layers: sampleLayers,
            footprints: [],
            tracks: [],
            vias: [],
            zones: [],
            nets: [],
          },
        },
        { projectId: "p1" },
      );
    });
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, layer_id: 0, color: "#ff8800" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    render(<LayerStack fetcher={fetcher} />);
    const picker = screen
      .getAllByTestId("layer-row")[0]!
      .querySelector("[data-testid='layer-color-picker']") as HTMLInputElement;
    await act(async () => {
      fireEvent.input(picker, { target: { value: "#ff8800" } });
      fireEvent.change(picker, { target: { value: "#ff8800" } });
    });
    expect(usePcbViewStore.getState().layerColors[0]).toBe("#ff8800");
    expect(fetcher).toHaveBeenCalled();
    expect(fetcher.mock.calls[0]![0]).toMatch(/ui_layer_color_set\/p1/);
    const sent = JSON.parse(
      (fetcher.mock.calls[0]![1] as RequestInit).body as string,
    ).args;
    expect(sent).toEqual({ layer_id: 0, color: "#ff8800" });
  });
});
