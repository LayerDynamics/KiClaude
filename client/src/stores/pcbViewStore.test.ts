import { describe, expect, it, beforeEach } from "vitest";

import { usePcbViewStore, getLayerView } from "./pcbViewStore";

const sampleLayers = [
  { id: 0, name: "F.Cu", kind: "copper" },
  { id: 31, name: "B.Cu", kind: "copper" },
  { id: 37, name: "F.SilkS", kind: "silkscreen" },
  { id: 44, name: "Edge.Cuts", kind: "outline" },
];

describe("pcbViewStore", () => {
  beforeEach(() => {
    usePcbViewStore.setState({
      layers: [],
      layerView: {},
      activeLayerId: null,
    });
  });

  it("setLayers picks the first copper layer as the default active", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    expect(usePcbViewStore.getState().activeLayerId).toBe(0);
  });

  it("setLayers preserves per-layer view overrides across reloads", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setLayerOpacity(31, 0.5);
    usePcbViewStore.getState().toggleLayerVisible(37);
    // Reload the same layer list — the per-id view should carry through.
    usePcbViewStore.getState().setLayers(sampleLayers);
    expect(usePcbViewStore.getState().layerView[31]?.opacity).toBe(0.5);
    expect(usePcbViewStore.getState().layerView[37]?.visible).toBe(false);
  });

  it("setLayers preserves the active layer when it survives the new list", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setActiveLayer(44);
    usePcbViewStore.getState().setLayers(sampleLayers);
    expect(usePcbViewStore.getState().activeLayerId).toBe(44);
  });

  it("setLayers falls back to the first copper layer when the active id vanishes", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setActiveLayer(44);
    // Edge.Cuts removed → active layer 44 no longer exists → fall back
    // to the first copper layer (id 0).
    usePcbViewStore.getState().setLayers(sampleLayers.slice(0, 3));
    expect(usePcbViewStore.getState().activeLayerId).toBe(0);
  });

  it("cycleActiveLayer wraps in both directions", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setActiveLayer(0);
    usePcbViewStore.getState().cycleActiveLayer(1);
    expect(usePcbViewStore.getState().activeLayerId).toBe(31);
    usePcbViewStore.getState().cycleActiveLayer(-1);
    expect(usePcbViewStore.getState().activeLayerId).toBe(0);
    usePcbViewStore.getState().cycleActiveLayer(-1);
    expect(usePcbViewStore.getState().activeLayerId).toBe(44);
    usePcbViewStore.getState().cycleActiveLayer(1);
    expect(usePcbViewStore.getState().activeLayerId).toBe(0);
  });

  it("cycleActiveLayer is a no-op on an empty stack", () => {
    usePcbViewStore.getState().cycleActiveLayer(1);
    expect(usePcbViewStore.getState().activeLayerId).toBeNull();
  });

  it("setLayerOpacity clamps to [0, 1]", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setLayerOpacity(0, 2.0);
    expect(usePcbViewStore.getState().layerView[0]?.opacity).toBe(1);
    usePcbViewStore.getState().setLayerOpacity(0, -0.5);
    expect(usePcbViewStore.getState().layerView[0]?.opacity).toBe(0);
    usePcbViewStore.getState().setLayerOpacity(0, 0.42);
    expect(usePcbViewStore.getState().layerView[0]?.opacity).toBe(0.42);
  });

  it("reorderLayer moves a layer to a target slot", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().reorderLayer(0, 37);
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([31, 37, 0, 44]);
  });

  it("reorderLayer is a no-op when moving onto itself or onto an unknown id", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().reorderLayer(0, 0);
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([0, 31, 37, 44]);
    usePcbViewStore.getState().reorderLayer(0, 9999);
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([0, 31, 37, 44]);
  });

  it("getLayerView falls back to defaults for unknown ids", () => {
    const view = getLayerView({ layerView: {} }, 999);
    expect(view.visible).toBe(true);
    expect(view.opacity).toBe(1);
  });
});
