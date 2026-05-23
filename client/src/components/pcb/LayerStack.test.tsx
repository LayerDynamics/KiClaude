import { cleanup, render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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

  it("drag-and-drop reorders the rows in the underlying store", () => {
    act(() => {
      usePcbViewStore.getState().setLayers(sampleLayers);
    });
    render(<LayerStack />);
    const rows = screen.getAllByTestId("layer-row");
    const fcu = rows[0]!;
    const silk = rows[2]!;
    // Programmatic DnD via DataTransfer mock.
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
    // Layer 0 (F.Cu) should now be at index 2 (where F.SilkS used to be).
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([31, 37, 0, 44]);
  });
});
