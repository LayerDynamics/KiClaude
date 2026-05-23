import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useProjectStore, type KcirProject } from "../../stores/projectStore";

import { NetClassPanel } from "./NetClassPanel";

const sampleProject: KcirProject = {
  kcir_version: "0.3",
  name: "blinky",
  metadata: { title: "blinky", revision: "", company: "", date: "" },
  net_classes: [
    { name: "Default", clearance_mm: 0.2, trace_width_mm: 0.25 },
    { name: "Power", clearance_mm: 0.3, trace_width_mm: 0.5 },
  ],
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

describe("NetClassPanel", () => {
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
    render(<NetClassPanel projectId="p1" fetcher={vi.fn()} />);
    expect(screen.getByTestId("net-class-panel").dataset.status).toBe("empty");
  });

  it("renders one row per existing net class", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<NetClassPanel projectId="p1" fetcher={vi.fn()} />);
    const rows = screen.getAllByTestId("netclass-row");
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.dataset.className)).toEqual(["Default", "Power"]);
  });

  it("editing a numeric input marks the row dirty and enables Save", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<NetClassPanel projectId="p1" fetcher={vi.fn()} />);
    const traceWidth = screen.getAllByTestId("netclass-trace-width")[1] as HTMLInputElement;
    fireEvent.change(traceWidth, { target: { value: "0.8" } });
    const row = screen.getAllByTestId("netclass-row")[1]!;
    expect(row.dataset.dirty).toBe("true");
    const save = row.querySelector("[data-testid='netclass-save']") as HTMLButtonElement;
    expect(save.disabled).toBe(false);
  });

  it("Save POSTs ui_netclass_set with the row's values + bind_nets CSV", async () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        net_class: { ...sampleProject.net_classes[1], trace_width_mm: 0.8 },
      }),
    );
    const cb = vi.fn();
    render(
      <NetClassPanel projectId="p1" fetcher={fetcher} onUpserted={cb} />,
    );
    const row = screen.getAllByTestId("netclass-row")[1]!;
    fireEvent.change(
      row.querySelector("[data-testid='netclass-trace-width']") as HTMLInputElement,
      { target: { value: "0.8" } },
    );
    fireEvent.change(
      row.querySelector("[data-testid='netclass-bind']") as HTMLInputElement,
      { target: { value: "+3V3, +5V" } },
    );
    await act(async () => {
      fireEvent.click(row.querySelector("[data-testid='netclass-save']")!);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]![0]).toMatch(/ui_netclass_set/);
    const sent = JSON.parse(
      (fetcher.mock.calls[0]![1] as RequestInit).body as string,
    ).args;
    expect(sent.name).toBe("Power");
    expect(sent.trace_width_mm).toBe(0.8);
    expect(sent.bind_nets).toEqual(["+3V3", "+5V"]);
    expect(cb).toHaveBeenCalledTimes(1);
    // Row is no longer dirty.
    await waitFor(() => {
      expect(row.dataset.dirty).toBe("false");
    });
  });

  it("Add appends a new row at the bottom", () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<NetClassPanel projectId="p1" fetcher={vi.fn()} />);
    expect(screen.getAllByTestId("netclass-row")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("netclass-add"));
    const rows = screen.getAllByTestId("netclass-row");
    expect(rows).toHaveLength(3);
    expect(rows[2]?.dataset.className).toBe("Class_3");
  });

  it("Delete POSTs ui_netclass_delete and removes the row on success", async () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: true, deleted: "Power", unbound_nets: [] }),
    );
    const cb = vi.fn();
    render(<NetClassPanel projectId="p1" fetcher={fetcher} onDeleted={cb} />);
    const row = screen.getAllByTestId("netclass-row")[1]!;
    await act(async () => {
      fireEvent.click(row.querySelector("[data-testid='netclass-delete']")!);
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/ui/ui_netclass_delete/p1",
      expect.objectContaining({ method: "POST" }),
    );
    expect(cb).toHaveBeenCalledWith("Power");
    await waitFor(() => {
      expect(screen.getAllByTestId("netclass-row")).toHaveLength(1);
    });
  });

  it("Delete on Default refuses without round-tripping", async () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    const fetcher = vi.fn();
    render(<NetClassPanel projectId="p1" fetcher={fetcher} />);
    const row = screen.getAllByTestId("netclass-row")[0]!;
    fireEvent.click(row.querySelector("[data-testid='netclass-delete']")!);
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.getByTestId("netclass-error").textContent).toMatch(/Default/);
  });

  it("surfaces gateway errors on save", async () => {
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({ ok: false, error: "name conflict" }, 400),
    );
    render(<NetClassPanel projectId="p1" fetcher={fetcher} />);
    const row = screen.getAllByTestId("netclass-row")[1]!;
    fireEvent.change(
      row.querySelector("[data-testid='netclass-trace-width']") as HTMLInputElement,
      { target: { value: "0.9" } },
    );
    await act(async () => {
      fireEvent.click(row.querySelector("[data-testid='netclass-save']")!);
    });
    expect(screen.getByTestId("netclass-error").textContent).toMatch(/name conflict/);
  });
});
