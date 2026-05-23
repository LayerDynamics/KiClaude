import { afterEach, describe, expect, it, vi } from "vitest";

import { _resetWasmLoaderForTests, loadKiclaudeWasm } from "./wasm";

// Mock both wasm-pack packages with synchronous JS objects.
vi.mock("kiclaude-ki", () => {
  return {
    default: vi.fn().mockResolvedValue({}),
    crateVersion: () => "0.1.0",
    kcirVersion: () => "0.1.0",
    openProjectFromStrings: (_pro: string, _pcb: string, fallback: string) => ({
      name: fallback,
      kcir_version: "0.1.0",
      pcb: { layers: [], footprints: [], tracks: [], vias: [], zones: [], nets: [] },
    }),
    emitPcbFromJson: (_json: string) => "(kicad_pcb (version 20240108))",
  };
});

vi.mock("kiclaude-cad", () => {
  return {
    default: vi.fn().mockResolvedValue({}),
    crateVersion: () => "0.1.0",
    polygonContainsPoint: () => true,
    polygonBoundingBox: () => ({ minX: 0, minY: 0, maxX: 1, maxY: 1 }),
  };
});

describe("loadKiclaudeWasm", () => {
  afterEach(() => _resetWasmLoaderForTests());

  it("returns ki + cad bindings", async () => {
    const { ki, cad } = await loadKiclaudeWasm();
    expect(typeof ki.crateVersion).toBe("function");
    expect(typeof cad.crateVersion).toBe("function");
    expect(ki.crateVersion()).toBe("0.1.0");
    expect(cad.crateVersion()).toBe("0.1.0");
  });

  it("memoizes init calls — second call resolves to the same bindings", async () => {
    const first = await loadKiclaudeWasm();
    const second = await loadKiclaudeWasm();
    expect(second).toBe(first);
  });

  it("ki.openProjectFromStrings returns a project dict containing the name 'blinky'", async () => {
    const { ki } = await loadKiclaudeWasm();
    const result = ki.openProjectFromStrings("{}", "(kicad_pcb)", "blinky") as { name: string };
    expect(result.name).toBe("blinky");
  });
});
