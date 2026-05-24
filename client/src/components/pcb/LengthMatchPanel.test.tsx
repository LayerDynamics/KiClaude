import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useProjectStore,
  type KcirLengthGroup,
  type KcirProject,
} from "../../stores/projectStore";

import {
  LengthMatchPanel,
  type LengthMatchReport,
} from "./LengthMatchPanel";

interface AnalyzerWasm {
  analyzeLengthMatch(pcb_json: string): string;
}

const ddrGroup: KcirLengthGroup = {
  name: "DDR3_DQ_BYTE0",
  nets: ["DQ0", "DQ1", "DQ2", "DQ3"],
  target_length_mm: 42.5,
  tolerance_mm: 0.127,
};

function buildProject(overrides: Partial<KcirProject> = {}): KcirProject {
  return {
    kcir_version: "0.4",
    name: "demo",
    metadata: { title: "", revision: "", company: "", date: "" },
    net_classes: [],
    pcb: {
      version: 1,
      generator: "kiclaude",
      layers: [{ id: 0, name: "F.Cu", kind: "copper" }],
      footprints: [],
      tracks: [
        { uuid: "t-dq0", net: "DQ0", width_mm: 0.2, points_mm: [[0, 0], [42.5, 0]] },
        { uuid: "t-dq1", net: "DQ1", width_mm: 0.2, points_mm: [[0, 0], [42.4, 0]] },
        { uuid: "t-dq2", net: "DQ2", width_mm: 0.2, points_mm: [[0, 0], [40.0, 0]] },
        // DQ3 deliberately unrouted.
      ],
      vias: [],
      zones: [],
      nets: [
        { name: "DQ0" },
        { name: "DQ1" },
        { name: "DQ2" },
        { name: "DQ3" },
        { name: "GND" },
      ],
      length_groups: [ddrGroup],
    },
    ...overrides,
  };
}

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Deterministic mock that runs the same bucket logic as the real
 * analyzer (M3-R-05) against the project's tracks + length_groups. */
function makeMockAnalyzer(): AnalyzerWasm {
  return {
    analyzeLengthMatch: vi.fn((pcbJson: string): string => {
      const pcb = JSON.parse(pcbJson) as {
        tracks: Array<{ net: string; points_mm: Array<[number, number]> }>;
        length_groups: KcirLengthGroup[];
      };
      const lengthOf = (net: string): number => {
        let total = 0;
        for (const t of pcb.tracks) {
          if (t.net !== net) continue;
          for (let i = 1; i < t.points_mm.length; i++) {
            const [x0, y0] = t.points_mm[i - 1]!;
            const [x1, y1] = t.points_mm[i]!;
            total += Math.hypot(x1 - x0, y1 - y0);
          }
        }
        return total;
      };
      const reports: LengthMatchReport[] = pcb.length_groups.map((g) => {
        const lengths = g.nets.map<[string, number]>((n) => [n, lengthOf(n)]);
        const effectiveTarget =
          g.target_length_mm > 0
            ? g.target_length_mm
            : Math.max(0, ...lengths.map(([, l]) => l));
        const members = lengths.map<LengthMatchReport["members"][number]>(([net, current]) => {
          if (current === 0) {
            return {
              net,
              current_length_mm: 0,
              delta_mm: -effectiveTarget,
              status: "unrouted",
              suggested_serpentine_count: 0,
              suggested_segment_gain_mm: 0,
            };
          }
          const delta = current - effectiveTarget;
          if (Math.abs(delta) <= g.tolerance_mm) {
            return {
              net,
              current_length_mm: current,
              delta_mm: delta,
              status: "in_range",
              suggested_serpentine_count: 0,
              suggested_segment_gain_mm: 0,
            };
          }
          if (delta < 0) {
            const shortfall = -delta;
            const n = Math.max(1, Math.ceil(shortfall / 5.0));
            return {
              net,
              current_length_mm: current,
              delta_mm: delta,
              status: "too_short",
              suggested_serpentine_count: n,
              suggested_segment_gain_mm: shortfall / n,
            };
          }
          return {
            net,
            current_length_mm: current,
            delta_mm: delta,
            status: "too_long",
            suggested_serpentine_count: 0,
            suggested_segment_gain_mm: 0,
          };
        });
        return { name: g.name, target_length_mm: effectiveTarget, tolerance_mm: g.tolerance_mm, members };
      });
      return JSON.stringify(reports);
    }),
  };
}

describe("LengthMatchPanel (M3-T-04)", () => {
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
      <LengthMatchPanel
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
      />,
    );
    expect(screen.getByTestId("length-match-panel").dataset.status).toBe("empty");
  });

  it("renders one row per declared length group, with CSV-joined members", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("length-match-panel").dataset.status).toBe("ready"),
    );
    const rows = screen.getAllByTestId("length-match-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.dataset.groupName).toBe("DDR3_DQ_BYTE0");
    const nets = rows[0]!.querySelector("[data-testid='length-match-nets']") as HTMLInputElement;
    expect(nets.value).toBe("DQ0, DQ1, DQ2, DQ3");
  });

  it("runs the analyzer and surfaces per-status counts", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const wasm = makeMockAnalyzer();
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() => expect(wasm.analyzeLengthMatch).toHaveBeenCalled());
    // DQ0=42.5 (in range), DQ1=42.4 (within 0.127 → in range),
    // DQ2=40 (too short by 2.5), DQ3=unrouted.
    expect(screen.getByTestId("length-match-status-inrange").textContent).toBe("✓2");
    expect(screen.getByTestId("length-match-status-short").textContent).toBe("−1");
    expect(screen.getByTestId("length-match-status-long").textContent).toBe("+0");
    expect(screen.getByTestId("length-match-status-unrouted").textContent).toBe("○1");
  });

  it("renders a serpentine suggestion for short members", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByTestId("length-match-report")).not.toBeNull(),
    );
    const dq2 = screen
      .getAllByTestId("length-match-report-member")
      .find((row) => row.dataset.netName === "DQ2");
    expect(dq2).toBeTruthy();
    // 2.5 mm shortfall / 5 mm cap → 1 segment × 2.5 mm.
    expect(dq2!.textContent).toContain("1 serpentines × 2.500 mm");
  });

  it("editing a row marks it dirty and enables Save", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("length-match-panel").dataset.status).toBe("ready"),
    );
    fireEvent.change(screen.getAllByTestId("length-match-tolerance")[0]!, {
      target: { value: "0.25" },
    });
    const row = screen.getAllByTestId("length-match-row")[0]!;
    expect(row.dataset.dirty).toBe("true");
  });

  it("Save POSTs ui_lengthgroup_set with split nets + numeric values", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, length_group: { ...ddrGroup, tolerance_mm: 0.25 } }),
    );
    const onUpserted = vi.fn();
    render(
      <LengthMatchPanel
        projectId="proj-7"
        fetcher={fetcher}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
        onUpserted={onUpserted}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("length-match-panel").dataset.status).toBe("ready"),
    );
    fireEvent.change(screen.getAllByTestId("length-match-tolerance")[0]!, {
      target: { value: "0.25" },
    });
    fireEvent.click(screen.getAllByTestId("length-match-save")[0]!);
    await waitFor(() => expect(onUpserted).toHaveBeenCalledTimes(1));
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/ui/ui_lengthgroup_set/proj-7");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.args.name).toBe("DDR3_DQ_BYTE0");
    expect(body.args.nets).toEqual(["DQ0", "DQ1", "DQ2", "DQ3"]);
    expect(body.args.tolerance_mm).toBe(0.25);
  });

  it("Delete POSTs ui_lengthgroup_delete and removes the row", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, deleted: "DDR3_DQ_BYTE0" }),
    );
    const onDeleted = vi.fn();
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={fetcher}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
        onDeleted={onDeleted}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("length-match-panel").dataset.status).toBe("ready"),
    );
    fireEvent.click(screen.getAllByTestId("length-match-delete")[0]!);
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("DDR3_DQ_BYTE0"));
    expect(JSON.parse(((fetcher.mock.calls[0]![1]) as RequestInit).body as string).args.name)
      .toBe("DDR3_DQ_BYTE0");
    expect(screen.queryAllByTestId("length-match-row")).toHaveLength(0);
  });

  it("Declare new group POSTs ui_lengthgroup_set with split CSV", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const newGroup: KcirLengthGroup = {
      name: "RGMII_TX",
      nets: ["TX0", "TX1"],
      target_length_mm: 0,
      tolerance_mm: 0.127,
    };
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, length_group: newGroup }),
    );
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={fetcher}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
      />,
    );
    fireEvent.change(screen.getByTestId("length-match-new-name"), {
      target: { value: "RGMII_TX" },
    });
    fireEvent.change(screen.getByTestId("length-match-new-nets"), {
      target: { value: "TX0, TX1, ,  " }, // trailing/whitespace tolerated
    });
    fireEvent.click(screen.getByTestId("length-match-declare"));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const body = JSON.parse(((fetcher.mock.calls[0]![1]) as RequestInit).body as string);
    expect(body.args.name).toBe("RGMII_TX");
    expect(body.args.nets).toEqual(["TX0", "TX1"]);
    expect(body.args.target_length_mm).toBe(0);
  });

  it("Declare validates name + at least one member locally before POST", () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn();
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={fetcher}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
      />,
    );
    fireEvent.click(screen.getByTestId("length-match-declare"));
    expect(screen.getByTestId("length-match-error").textContent ?? "").toContain("name");
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("length-match-new-name"), {
      target: { value: "G" },
    });
    fireEvent.click(screen.getByTestId("length-match-declare"));
    expect(screen.getByTestId("length-match-error").textContent ?? "").toMatch(/member|net/);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("Server error on Save is surfaced and the row stays dirty", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: false, error: "net 'BOGUS' not found on this board" }, 400),
    );
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={fetcher}
        wasmLoader={() => Promise.resolve({ cad: makeMockAnalyzer() })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("length-match-panel").dataset.status).toBe("ready"),
    );
    fireEvent.change(screen.getAllByTestId("length-match-tolerance")[0]!, {
      target: { value: "0.25" },
    });
    fireEvent.click(screen.getAllByTestId("length-match-save")[0]!);
    await waitFor(() =>
      expect(screen.queryByTestId("length-match-error")).not.toBeNull(),
    );
    expect(screen.getByTestId("length-match-error").textContent ?? "").toContain("BOGUS");
    expect(screen.getAllByTestId("length-match-row")[0]!.dataset.dirty).toBe("true");
  });

  it("Wasm load failure shows the analyzer banner but the editor still works", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(
      <LengthMatchPanel
        projectId="p1"
        fetcher={vi.fn()}
        wasmLoader={() => Promise.reject(new Error("wasm not built"))}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByTestId("length-match-wasm-error")).not.toBeNull(),
    );
    expect(screen.getByTestId("length-match-wasm-error").textContent ?? "").toContain(
      "wasm not built",
    );
    // Editor row still rendered (no analyzer needed for declaration).
    expect(screen.getAllByTestId("length-match-row")).toHaveLength(1);
  });
});
