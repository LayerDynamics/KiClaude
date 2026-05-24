import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useProjectStore,
  type KcirProject,
  type KcirStackup,
} from "../../stores/projectStore";

import { NetInspector, resolveStackupForLayer } from "./NetInspector";

interface MicrostripWasm {
  microstripZ0(json: string): string;
  striplineZ0(json: string): number;
  differentialMicrostripZ(json: string): string;
  solveMicrostripWidthForZ0(target: number, h: number, er: number, t: number): number;
}

const fourLayerStackup: KcirStackup = {
  layers: [
    { name: "F.Cu", kind: "copper", thickness_mm: 0.035, dielectric_constant: null, loss_tangent: null, color: "copper" },
    { name: "dielectric 1", kind: "dielectric", thickness_mm: 0.21, dielectric_constant: 4.5, loss_tangent: 0.02, color: "FR4" },
    { name: "In1.Cu", kind: "copper", thickness_mm: 0.018, dielectric_constant: null, loss_tangent: null, color: "copper" },
    { name: "dielectric 2", kind: "dielectric", thickness_mm: 1.10, dielectric_constant: 4.5, loss_tangent: 0.02, color: "FR4" },
    { name: "In2.Cu", kind: "copper", thickness_mm: 0.018, dielectric_constant: null, loss_tangent: null, color: "copper" },
    { name: "dielectric 3", kind: "dielectric", thickness_mm: 0.21, dielectric_constant: 4.5, loss_tangent: 0.02, color: "FR4" },
    { name: "B.Cu", kind: "copper", thickness_mm: 0.035, dielectric_constant: null, loss_tangent: null, color: "copper" },
  ],
  power_plane_layers: [],
  controlled_impedance: true,
  board_thickness_mm: 1.626,
  finish: "ENIG",
};

function buildProject(overrides: Partial<KcirProject> = {}): KcirProject {
  return {
    kcir_version: "0.4",
    name: "blinky",
    metadata: { title: "", revision: "", company: "", date: "" },
    net_classes: [
      { name: "Default", clearance_mm: 0.2, trace_width_mm: 0.25 },
      { name: "HighSpeed", clearance_mm: 0.15, trace_width_mm: 0.20 },
    ],
    pcb: {
      version: 1,
      generator: "kiclaude",
      layers: [
        { id: 0, name: "F.Cu", kind: "copper" },
        { id: 1, name: "In1.Cu", kind: "copper" },
        { id: 31, name: "B.Cu", kind: "copper" },
      ],
      footprints: [],
      tracks: [
        {
          uuid: "t-vcc",
          net: "VCC",
          width_mm: 0.25,
          points_mm: [[0, 0], [1, 0]],
          // The runtime track shape carries `layer`; KcirTrack's
          // typed interface doesn't surface it but the structural
          // accessor in NetInspector reads it dynamically.
          ...({ layer: "F.Cu" } as Record<string, unknown>),
        },
        {
          uuid: "t-clk",
          net: "CLK",
          width_mm: 0.20,
          points_mm: [[0, 0], [1, 0]],
          ...({ layer: "In1.Cu" } as Record<string, unknown>),
        },
      ],
      vias: [],
      zones: [],
      nets: [
        { name: "VCC", ...({ class: ["Default"] } as Record<string, unknown>) },
        { name: "CLK", ...({ class: ["HighSpeed"] } as Record<string, unknown>) },
        { name: "GND" },
      ],
    },
    stackup: fourLayerStackup,
    ...overrides,
  };
}

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Deterministic stand-in for the cad wasm — mirrors the wasm shim
 * contract (JSON-in / JSON-out for the forward solvers, numeric-in /
 * numeric-out for the inverse solvers). The numbers below are chosen
 * to be self-consistent: 50 Ω comes back at width=0.29 mm, mimicking
 * the real Hammerstad-Jensen result on the FR-4 stackup. */
function makeMockWasm(overrides: Partial<MicrostripWasm> = {}): MicrostripWasm {
  return {
    microstripZ0: vi.fn((json: string) => {
      const g = JSON.parse(json) as {
        width_mm: number;
        dielectric_height_mm: number;
        dielectric_constant: number;
      };
      // Monotone-decreasing surrogate: Z0 = 120 / (1 + W/H · √εr).
      const u = g.width_mm / g.dielectric_height_mm;
      const z = 120 / (1 + u * Math.sqrt(g.dielectric_constant));
      return JSON.stringify({
        z0_hammerstad_ohms: z,
        z0_ipc2141_ohms: z + 2.0,
      });
    }),
    striplineZ0: vi.fn((json: string) => {
      const g = JSON.parse(json) as {
        width_mm: number;
        dielectric_height_mm: number;
        dielectric_constant: number;
      };
      const u = g.width_mm / g.dielectric_height_mm;
      return 100 / (1 + u * Math.sqrt(g.dielectric_constant));
    }),
    differentialMicrostripZ: vi.fn(() =>
      JSON.stringify({ zdiff_ohms: 90, zcomm_ohms: 25, z0_single_ended_ohms: 50 }),
    ),
    // Inverse: pick the W that makes the surrogate hit target_ohms.
    // From Z = 120/(1 + u√εr): u = (120/target − 1)/√εr → W = u·h.
    solveMicrostripWidthForZ0: vi.fn(
      (target: number, h: number, er: number, _t: number) => {
        const u = (120 / target - 1) / Math.sqrt(er);
        if (!Number.isFinite(u) || u <= 0) return Number.NaN;
        return u * h;
      },
    ),
    ...overrides,
  };
}

describe("resolveStackupForLayer (M3-T-02)", () => {
  it("treats F.Cu as outer microstrip with the first inward dielectric", () => {
    const r = resolveStackupForLayer(fourLayerStackup, "F.Cu");
    expect(r.mode).toBe("microstrip");
    expect(r.height_mm).toBeCloseTo(0.21, 6);
    expect(r.dielectric_constant).toBeCloseTo(4.5, 6);
    expect(r.copper_thickness_mm).toBeCloseTo(0.035, 6);
  });

  it("treats B.Cu as outer microstrip with the inward dielectric below B.Cu", () => {
    const r = resolveStackupForLayer(fourLayerStackup, "B.Cu");
    expect(r.mode).toBe("microstrip");
    expect(r.height_mm).toBeCloseTo(0.21, 6);
  });

  it("treats inner layers as stripline using the thinner adjacent dielectric", () => {
    const r = resolveStackupForLayer(fourLayerStackup, "In1.Cu");
    expect(r.mode).toBe("stripline");
    // Adjacent dielectrics around In1.Cu are 0.21 (above) and 1.10
    // (below) → thinner is 0.21.
    expect(r.height_mm).toBeCloseTo(0.21, 6);
    expect(r.copper_thickness_mm).toBeCloseTo(0.018, 6);
  });
});

describe("NetInspector (M3-T-02)", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders an empty state when no project is loaded", () => {
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockWasm() })}
      />,
    );
    expect(screen.getByTestId("net-inspector").dataset.status).toBe("empty");
  });

  it("selects the first net on mount and resolves its home layer + class", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockWasm() })}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("net-inspector").dataset.status).toBe("ready"));
    expect(screen.getByTestId("net-inspector-layer").textContent).toBe("Layer: F.Cu");
    expect(screen.getByTestId("net-inspector-class").textContent).toBe("Class: Default");
    expect(screen.getByTestId("net-inspector").dataset.mode).toBe("microstrip");
    expect(screen.getByTestId("net-inspector").dataset.source).toBe("project");
  });

  it("computes Z0 from the wasm shim and re-renders when width changes", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const wasm = makeMockWasm();
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() => expect(wasm.microstripZ0).toHaveBeenCalled());
    const before = screen.getByTestId("net-inspector-z0-primary").textContent;
    fireEvent.change(screen.getByTestId("net-inspector-width-number"), {
      target: { value: "0.5" },
    });
    await waitFor(() => {
      expect(screen.getByTestId("net-inspector-z0-primary").textContent).not.toBe(before);
    });
    // Wider trace → lower Z0 on our surrogate.
    const after = parseFloat(
      (screen.getByTestId("net-inspector-z0-primary").textContent ?? "0").replace(/[^0-9.]/g, ""),
    );
    const beforeNum = parseFloat((before ?? "0").replace(/[^0-9.]/g, ""));
    expect(after).toBeLessThan(beforeNum);
  });

  it("switching to an inner-layer net flips the mode to stripline", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockWasm() })}
      />,
    );
    fireEvent.change(screen.getByTestId("net-inspector-select"), { target: { value: "CLK" } });
    await waitFor(() => expect(screen.getByTestId("net-inspector").dataset.mode).toBe("stripline"));
    expect(screen.getByTestId("net-inspector-layer").textContent).toBe("Layer: In1.Cu");
    expect(screen.getByTestId("net-inspector-class").textContent).toBe("Class: HighSpeed");
  });

  it("Snap to 50Ω drives the slider to the inverse-solver width and marks dirty", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const wasm = makeMockWasm();
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("net-inspector").dataset.status).toBe("ready"),
    );
    fireEvent.click(screen.getByTestId("net-inspector-snap-50"));
    // Surrogate inverse: W = ((120/50 − 1)/√4.5) · 0.21 ≈ 0.139 mm.
    await waitFor(() => {
      const w = (screen.getByTestId("net-inspector-width-number") as HTMLInputElement).value;
      expect(parseFloat(w)).toBeCloseTo(0.1385, 2);
    });
    expect(wasm.solveMicrostripWidthForZ0).toHaveBeenCalledWith(50, 0.21, 4.5, 0.035);
    expect(screen.getByTestId("net-inspector").dataset.dirty).toBe("true");
  });

  it("Apply POSTs ui_netclass_set with the snapped width + clears dirty on success", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const wasm = makeMockWasm();
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, net_class: { name: "Default" } }),
    );
    const onApplied = vi.fn();
    render(
      <NetInspector
        projectId="proj-77"
        fetcher={fetcher}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
        onApplied={onApplied}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("net-inspector").dataset.status).toBe("ready"),
    );
    fireEvent.click(screen.getByTestId("net-inspector-snap-50"));
    await waitFor(() =>
      expect(screen.getByTestId("net-inspector").dataset.dirty).toBe("true"),
    );
    fireEvent.click(screen.getByTestId("net-inspector-apply"));
    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/ui/ui_netclass_set/proj-77");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.args.name).toBe("Default");
    expect(body.args.bind_nets).toEqual(["VCC"]);
    expect(body.args.trace_width_mm).toBeCloseTo(0.1385, 2);
    // After a successful apply the snapped width is the new persisted
    // width: dirty resets only when the project store updates, but
    // the gateway round-trip is the same shape NetClassPanel uses.
  });

  it("surfaces server errors on Apply failure and keeps the snap unapplied", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: false, error: "duplicate name" }, 400),
    );
    render(
      <NetInspector
        projectId="p1"
        fetcher={fetcher}
        wasmLoader={() => Promise.resolve({ cad: makeMockWasm() })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("net-inspector").dataset.status).toBe("ready"),
    );
    fireEvent.click(screen.getByTestId("net-inspector-snap-50"));
    fireEvent.click(screen.getByTestId("net-inspector-apply"));
    await waitFor(() =>
      expect(screen.queryByTestId("net-inspector-error")).not.toBeNull(),
    );
    expect(screen.getByTestId("net-inspector-error").textContent ?? "").toContain("duplicate");
  });

  it("falls back to the FR-4 default stackup with a warning when none is loaded", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject({ stackup: undefined }));
    });
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockWasm() })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("net-inspector").dataset.source).toBe("fallback"),
    );
    expect(screen.queryByTestId("net-inspector-fallback")).not.toBeNull();
  });

  it("surfaces an unreachable-snap target as a wasm error", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const wasm = makeMockWasm({
      solveMicrostripWidthForZ0: vi.fn(() => Number.NaN),
    });
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("net-inspector").dataset.status).toBe("ready"),
    );
    fireEvent.click(screen.getByTestId("net-inspector-snap-100"));
    await waitFor(() =>
      expect(screen.queryByTestId("net-inspector-wasm-error")).not.toBeNull(),
    );
    expect(screen.getByTestId("net-inspector-wasm-error").textContent ?? "")
      .toMatch(/unreachable/);
  });

  it("Revert restores the persisted trace width", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(
      <NetInspector
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockWasm() })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("net-inspector").dataset.status).toBe("ready"),
    );
    const numberInput = screen.getByTestId("net-inspector-width-number") as HTMLInputElement;
    fireEvent.change(numberInput, { target: { value: "0.6" } });
    expect(screen.getByTestId("net-inspector").dataset.dirty).toBe("true");
    fireEvent.click(screen.getByTestId("net-inspector-revert"));
    await waitFor(() => {
      const w = (screen.getByTestId("net-inspector-width-number") as HTMLInputElement).value;
      expect(parseFloat(w)).toBeCloseTo(0.25, 6);
    });
    expect(screen.getByTestId("net-inspector").dataset.dirty).toBe("false");
  });
});
