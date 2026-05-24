import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useProjectStore,
  type KcirProject,
  type KcirStackup,
} from "../../stores/projectStore";

import { StackupEditor } from "./StackupEditor";

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

const sampleProject: KcirProject = {
  kcir_version: "0.4",
  name: "controlled_impedance_board",
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
    nets: [],
  },
  stackup: fourLayerStackup,
};

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("StackupEditor (M3-T-01)", () => {
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
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    expect(screen.getByTestId("stackup-editor").dataset.status).toBe("empty");
  });

  it("renders one row per stackup layer in declared order", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const rows = screen.getAllByTestId("stackup-row");
    expect(rows.map((r) => r.dataset.layerName)).toEqual([
      "F.Cu",
      "dielectric 1",
      "In1.Cu",
      "dielectric 2",
      "In2.Cu",
      "dielectric 3",
      "B.Cu",
    ]);
  });

  it("shows the live board thickness (sum of layer thicknesses)", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const total = screen.getByTestId("stackup-board-thickness").textContent ?? "";
    // 0.035 + 0.21 + 0.018 + 1.10 + 0.018 + 0.21 + 0.035 = 1.626
    expect(total).toMatch(/1\.626/);
  });

  it("typing into a thickness input updates the live board thickness", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const thicknesses = screen.getAllByTestId("stackup-thickness");
    // Bump dielectric 2 (index 3) from 1.10 to 1.50 → +0.40
    fireEvent.change(thicknesses[3]!, { target: { value: "1.5" } });
    expect(screen.getByTestId("stackup-board-thickness").textContent ?? "").toMatch(/2\.026/);
  });

  it("any edit marks the editor dirty and enables Save", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const editor = screen.getByTestId("stackup-editor");
    expect(editor.dataset.dirty).toBe("false");
    const finish = screen.getByTestId("stackup-finish") as HTMLInputElement;
    fireEvent.change(finish, { target: { value: "HASL" } });
    expect(editor.dataset.dirty).toBe("true");
    const save = screen.getByTestId("stackup-save") as HTMLButtonElement;
    expect(save.disabled).toBe(false);
  });

  it("copper layers disable the εr / loss-tan inputs", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const epsilons = screen.getAllByTestId("stackup-epsilon");
    // F.Cu (0), In1.Cu (2), In2.Cu (4), B.Cu (6) are copper.
    [0, 2, 4, 6].forEach((i) => {
      expect((epsilons[i] as HTMLInputElement).disabled).toBe(true);
    });
    // dielectric 1 (1), 2 (3), 3 (5) are dielectric.
    [1, 3, 5].forEach((i) => {
      expect((epsilons[i] as HTMLInputElement).disabled).toBe(false);
    });
  });

  it("Add inserts a new dielectric row at the bottom and marks dirty", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    fireEvent.click(screen.getByTestId("stackup-add"));
    const rows = screen.getAllByTestId("stackup-row");
    expect(rows).toHaveLength(8);
    expect(rows[7]!.dataset.layerKind).toBe("dielectric");
    expect(screen.getByTestId("stackup-editor").dataset.dirty).toBe("true");
  });

  it("Move ▲ swaps two adjacent layers", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const ups = screen.getAllByTestId("stackup-move-up");
    // Move 'In1.Cu' (index 2) up over 'dielectric 1' (index 1).
    fireEvent.click(ups[2]!);
    const rows = screen.getAllByTestId("stackup-row");
    expect(rows[1]!.dataset.layerName).toBe("In1.Cu");
    expect(rows[2]!.dataset.layerName).toBe("dielectric 1");
  });

  it("Delete drops a row and renumbers", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const dels = screen.getAllByTestId("stackup-delete");
    fireEvent.click(dels[2]!); // delete In1.Cu
    const rows = screen.getAllByTestId("stackup-row");
    expect(rows).toHaveLength(6);
    expect(rows.map((r) => r.dataset.layerName)).toEqual([
      "F.Cu",
      "dielectric 1",
      "dielectric 2",
      "In2.Cu",
      "dielectric 3",
      "B.Cu",
    ]);
  });

  it("Revert restores the project's persisted stackup", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    const finish = screen.getByTestId("stackup-finish") as HTMLInputElement;
    fireEvent.change(finish, { target: { value: "OSP" } });
    expect(finish.value).toBe("OSP");
    fireEvent.click(screen.getByTestId("stackup-revert"));
    expect((screen.getByTestId("stackup-finish") as HTMLInputElement).value).toBe("ENIG");
    expect(screen.getByTestId("stackup-editor").dataset.dirty).toBe("false");
  });

  it("Save POSTs ui_stackup_set with the working copy and clears dirty on 200", async () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    const echoedStackup = { ...fourLayerStackup, finish: "OSP", board_thickness_mm: 1.626 };
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, stackup: echoedStackup }),
    );
    const onSaved = vi.fn();
    render(<StackupEditor projectId="proj-42" fetcher={fetcher} onSaved={onSaved} />);

    const finish = screen.getByTestId("stackup-finish") as HTMLInputElement;
    fireEvent.change(finish, { target: { value: "OSP" } });
    fireEvent.click(screen.getByTestId("stackup-save"));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onSaved.mock.calls[0]![0].finish).toBe("OSP");

    expect(fetcher).toHaveBeenCalledTimes(1);
    const [calledUrl, init] = fetcher.mock.calls[0]!;
    expect(calledUrl).toBe("/api/ui/ui_stackup_set/proj-42");
    expect((init as RequestInit).method).toBe("POST");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.args.finish).toBe("OSP");
    expect(body.args.layers).toHaveLength(7);
    expect(body.args.controlled_impedance).toBe(true);

    expect(screen.getByTestId("stackup-editor").dataset.dirty).toBe("false");
  });

  it("Save surfaces server errors and stays dirty so the user can retry", async () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: false, error: "first copper layer must be `F.Cu`, got 'B.Cu'" }, 400),
    );
    render(<StackupEditor projectId="p1" fetcher={fetcher} />);
    fireEvent.change(screen.getByTestId("stackup-finish"), { target: { value: "OSP" } });
    fireEvent.click(screen.getByTestId("stackup-save"));
    await waitFor(() => expect(screen.queryByTestId("stackup-error")).not.toBeNull());
    expect(screen.getByTestId("stackup-error").textContent ?? "").toContain("F.Cu");
    expect(screen.getByTestId("stackup-editor").dataset.dirty).toBe("true");
  });

  it("resyncs when the project's stackup changes underneath it", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<StackupEditor projectId="p1" fetcher={vi.fn()} />);
    expect(screen.getAllByTestId("stackup-row")).toHaveLength(7);
    act(() => {
      useProjectStore.getState().setProject({
        ...sampleProject,
        stackup: {
          ...fourLayerStackup,
          layers: fourLayerStackup.layers.slice(0, 3).concat(fourLayerStackup.layers.slice(-1)),
          board_thickness_mm: 0.298,
        },
      });
    });
    expect(screen.getAllByTestId("stackup-row")).toHaveLength(4);
  });
});
