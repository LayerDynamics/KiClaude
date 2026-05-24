import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useProjectStore,
  type KcirDiffPair,
  type KcirProject,
} from "../../stores/projectStore";

import { DiffPairPanel, DIFFPAIR_PRESETS } from "./DiffPairPanel";

const usbPair: KcirDiffPair = {
  name: "USB_D",
  net_positive: "USB_D+",
  net_negative: "USB_D-",
  target_impedance_ohms: 90,
  target_gap_mm: 0.127,
  length_group: "USB",
  skew_tolerance_mm: 0.127,
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
      tracks: [],
      vias: [],
      zones: [],
      nets: [
        { name: "USB_D+" },
        { name: "USB_D-" },
        { name: "PCIE0+" },
        { name: "PCIE0-" },
        { name: "GND" },
      ],
      diff_pairs: [usbPair],
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

describe("DiffPairPanel (M3-T-03)", () => {
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
    render(<DiffPairPanel projectId="p1" fetcher={vi.fn()} />);
    expect(screen.getByTestId("diffpair-panel").dataset.status).toBe("empty");
  });

  it("renders one row per declared diff pair", () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(<DiffPairPanel projectId="p1" fetcher={vi.fn()} />);
    const rows = screen.getAllByTestId("diffpair-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.dataset.pairName).toBe("USB_D");
    expect(screen.getByTestId("diffpair-count").textContent).toBe("1 declared");
  });

  it("net dropdowns offer every named net on the board", () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(<DiffPairPanel projectId="p1" fetcher={vi.fn()} />);
    const newPositive = screen.getByTestId("diffpair-new-positive") as HTMLSelectElement;
    const optionValues = Array.from(newPositive.options).map((o) => o.value);
    expect(optionValues).toEqual(["", "USB_D+", "USB_D-", "PCIE0+", "PCIE0-", "GND"]);
  });

  it("Editing a row marks it dirty and enables Save", () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(<DiffPairPanel projectId="p1" fetcher={vi.fn()} />);
    const zdiff = screen.getAllByTestId("diffpair-zdiff")[0] as HTMLInputElement;
    fireEvent.change(zdiff, { target: { value: "85" } });
    const row = screen.getAllByTestId("diffpair-row")[0]!;
    expect(row.dataset.dirty).toBe("true");
    const save = row.querySelector("[data-testid='diffpair-save']") as HTMLButtonElement;
    expect(save.disabled).toBe(false);
  });

  it("Save POSTs ui_diffpair_set with the row's payload", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, diff_pair: { ...usbPair, target_impedance_ohms: 85 } }),
    );
    const onUpserted = vi.fn();
    render(<DiffPairPanel projectId="proj-9" fetcher={fetcher} onUpserted={onUpserted} />);
    const zdiff = screen.getAllByTestId("diffpair-zdiff")[0] as HTMLInputElement;
    fireEvent.change(zdiff, { target: { value: "85" } });
    fireEvent.click(screen.getAllByTestId("diffpair-save")[0]!);
    await waitFor(() => expect(onUpserted).toHaveBeenCalledTimes(1));
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/ui/ui_diffpair_set/proj-9");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.args.name).toBe("USB_D");
    expect(body.args.target_impedance_ohms).toBe(85);
    expect(body.args.net_positive).toBe("USB_D+");
    expect(body.args.net_negative).toBe("USB_D-");
  });

  it("Delete POSTs ui_diffpair_delete and drops the row", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, deleted: "USB_D", cleared_back_refs: ["USB_D+", "USB_D-"] }),
    );
    const onDeleted = vi.fn();
    render(<DiffPairPanel projectId="p1" fetcher={fetcher} onDeleted={onDeleted} />);
    fireEvent.click(screen.getAllByTestId("diffpair-delete")[0]!);
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("USB_D"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/ui/ui_diffpair_delete/p1");
    expect(JSON.parse((init as RequestInit).body as string).args.name).toBe("USB_D");
    expect(screen.queryAllByTestId("diffpair-row")).toHaveLength(0);
  });

  it("Declare new pair POSTs ui_diffpair_set and appends a row", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const newPair: KcirDiffPair = {
      name: "PCIE0",
      net_positive: "PCIE0+",
      net_negative: "PCIE0-",
      target_impedance_ohms: 100,
      target_gap_mm: 0.150,
      length_group: "",
      skew_tolerance_mm: 0.050,
    };
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, diff_pair: newPair }),
    );
    render(<DiffPairPanel projectId="p1" fetcher={fetcher} />);
    fireEvent.change(screen.getByTestId("diffpair-new-name"), { target: { value: "PCIE0" } });
    fireEvent.change(screen.getByTestId("diffpair-new-positive"), { target: { value: "PCIE0+" } });
    fireEvent.change(screen.getByTestId("diffpair-new-negative"), { target: { value: "PCIE0-" } });
    // Pick the PCIe 100 Ω preset.
    fireEvent.change(screen.getByTestId("diffpair-preset"), {
      target: { value: "PCIe 1.x-3.x (100 Ω)" },
    });
    // Preset should have updated the Zdiff / gap / skew inputs.
    expect((screen.getByTestId("diffpair-new-zdiff") as HTMLInputElement).value).toBe("100");
    expect((screen.getByTestId("diffpair-new-gap") as HTMLInputElement).value).toBe("0.15");
    expect((screen.getByTestId("diffpair-new-skew") as HTMLInputElement).value).toBe("0.05");
    fireEvent.click(screen.getByTestId("diffpair-declare"));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const body = JSON.parse(((fetcher.mock.calls[0]![1]) as RequestInit).body as string);
    expect(body.args.name).toBe("PCIE0");
    expect(body.args.net_positive).toBe("PCIE0+");
    expect(body.args.net_negative).toBe("PCIE0-");
    expect(body.args.target_impedance_ohms).toBe(100);
    expect(body.args.target_gap_mm).toBe(0.15);
    // Row appears immediately on success.
    await waitFor(() =>
      expect(screen.getAllByTestId("diffpair-row").map((r) => r.dataset.pairName)).toContain(
        "PCIE0",
      ),
    );
  });

  it("Declare validates name + both legs locally before any POST", () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn();
    render(<DiffPairPanel projectId="p1" fetcher={fetcher} />);
    fireEvent.click(screen.getByTestId("diffpair-declare"));
    expect(screen.getByTestId("diffpair-error").textContent ?? "").toContain("name");
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("diffpair-new-name"), { target: { value: "USB_D2" } });
    fireEvent.click(screen.getByTestId("diffpair-declare"));
    expect(screen.getByTestId("diffpair-error").textContent ?? "").toMatch(/nets/);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("Server errors on Save are surfaced and the row stays dirty", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: false, error: "net 'USB_D+' is already declared in pair 'X'" }, 400),
    );
    render(<DiffPairPanel projectId="p1" fetcher={fetcher} />);
    fireEvent.change(screen.getAllByTestId("diffpair-zdiff")[0]!, { target: { value: "85" } });
    fireEvent.click(screen.getAllByTestId("diffpair-save")[0]!);
    await waitFor(() => expect(screen.queryByTestId("diffpair-error")).not.toBeNull());
    expect(screen.getByTestId("diffpair-error").textContent ?? "").toContain("already declared");
    const row = screen.getAllByTestId("diffpair-row")[0]!;
    expect(row.dataset.dirty).toBe("true");
  });

  it("Preset list covers USB/LVDS/PCIe/SATA as exposed constants", () => {
    // Numeric sort (default sort is lexicographic — "100" < "85").
    expect(
      DIFFPAIR_PRESETS.map((p) => p.zdiff_ohms).sort((a, b) => a - b),
    ).toEqual([85, 90, 100, 100]);
  });

  it("Re-syncs the rows when the project's diff_pairs change underneath it", () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    render(<DiffPairPanel projectId="p1" fetcher={vi.fn()} />);
    expect(screen.getAllByTestId("diffpair-row")).toHaveLength(1);
    act(() => {
      useProjectStore.getState().setProject(
        buildProject({
          pcb: {
            ...buildProject().pcb,
            diff_pairs: [
              { ...usbPair },
              {
                name: "USB_D2",
                net_positive: "PCIE0+",
                net_negative: "PCIE0-",
                target_impedance_ohms: 100,
                target_gap_mm: 0.15,
                length_group: "",
                skew_tolerance_mm: 0.05,
              },
            ],
          },
        }),
      );
    });
    expect(screen.getAllByTestId("diffpair-row")).toHaveLength(2);
    expect(
      screen.getAllByTestId("diffpair-row").map((r) => r.dataset.pairName),
    ).toEqual(["USB_D", "USB_D2"]);
  });
});
