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

  it("reorderLayer refuses to move when F.Cu or B.Cu would be involved (KiCad stackup anchors)", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    // Moving F.Cu off slot 0 should fail.
    expect(usePcbViewStore.getState().reorderLayer(0, 37)).toBe(false);
    // Moving onto B.Cu (id 31) should also fail.
    expect(usePcbViewStore.getState().reorderLayer(37, 31)).toBe(false);
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([0, 31, 37, 44]);
  });

  it("reorderLayer refuses cross-kind moves (silkscreen can't slot between copper)", () => {
    // Build a stack with two inner copper layers to give a legal move
    // before testing the refusal.
    const withInners = [
      { id: 0, name: "F.Cu", kind: "copper" },
      { id: 1, name: "In1.Cu", kind: "copper" },
      { id: 2, name: "In2.Cu", kind: "copper" },
      { id: 31, name: "B.Cu", kind: "copper" },
      { id: 37, name: "F.SilkS", kind: "silkscreen" },
    ];
    usePcbViewStore.getState().setLayers(withInners);
    // Silk → copper: refused.
    expect(usePcbViewStore.getState().reorderLayer(37, 1)).toBe(false);
    // Inner copper swap (same kind, neither is F.Cu/B.Cu): allowed.
    expect(usePcbViewStore.getState().reorderLayer(1, 2)).toBe(true);
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([0, 2, 1, 31, 37]);
  });

  it("reorderLayer is a no-op when moving onto itself or onto an unknown id", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    expect(usePcbViewStore.getState().reorderLayer(37, 37)).toBe(false);
    expect(usePcbViewStore.getState().reorderLayer(37, 9999)).toBe(false);
    expect(
      usePcbViewStore.getState().layers.map((l) => l.id),
    ).toEqual([0, 31, 37, 44]);
  });

  it("setLayerColor stores normalised hex strings and rejects malformed input", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setLayerColor(0, "#FF8800");
    expect(usePcbViewStore.getState().layerColors[0]).toBe("#ff8800");
    // Invalid input is ignored (no throw, no overwrite).
    usePcbViewStore.getState().setLayerColor(0, "not-a-colour");
    expect(usePcbViewStore.getState().layerColors[0]).toBe("#ff8800");
    usePcbViewStore.getState().setLayerColor(0, "#zzzzzz");
    expect(usePcbViewStore.getState().layerColors[0]).toBe("#ff8800");
  });

  it("setLayerColors bulk-replaces and filters malformed entries", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setLayerColors({
      0: "#abcdef",
      31: "not-a-hex",
      "37": "#112233",
    } as unknown as Record<number, string>);
    const colours = usePcbViewStore.getState().layerColors;
    expect(colours[0]).toBe("#abcdef");
    expect(colours[31]).toBeUndefined();
    expect(colours[37]).toBe("#112233");
  });

  it("setLayers preserves per-layer colour overrides across reloads", () => {
    usePcbViewStore.getState().setLayers(sampleLayers);
    usePcbViewStore.getState().setLayerColor(31, "#abcdef");
    usePcbViewStore.getState().setLayers(sampleLayers);
    expect(usePcbViewStore.getState().layerColors[31]).toBe("#abcdef");
  });

  it("getLayerView falls back to defaults for unknown ids", () => {
    const view = getLayerView({ layerView: {} }, 999);
    expect(view.visible).toBe(true);
    expect(view.opacity).toBe(1);
  });
});
