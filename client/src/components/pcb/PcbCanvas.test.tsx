import { cleanup, render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useProjectStore, type KcirProject } from "../../stores/projectStore";
import { usePcbViewStore } from "../../stores/pcbViewStore";
import { useSelectionStore } from "../../stores/selectionStore";

import { PcbCanvas } from "./PcbCanvas";

const sampleProject: KcirProject = {
  kcir_version: "0.3",
  name: "blinky",
  metadata: { title: "blinky", revision: "", company: "", date: "" },
  net_classes: [],
  pcb: {
    version: 1,
    generator: "kiclaude",
    layers: [
      { id: 0, name: "F.Cu", kind: "copper" },
      { id: 31, name: "B.Cu", kind: "copper" },
      { id: 37, name: "F.SilkS", kind: "silkscreen" },
      { id: 44, name: "Edge.Cuts", kind: "outline" },
    ],
    footprints: [],
    tracks: [],
    vias: [],
    zones: [],
    nets: [],
  },
};

describe("PcbCanvas", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
      useSelectionStore.getState().clear();
    });
  });
  afterEach(() => cleanup());

  it("renders the loading state until the loader resolves", async () => {
    let resolveLoad: (value: { status: "ready"; cached: boolean }) => void = () => {};
    const loader = vi.fn().mockImplementation(
      () =>
        new Promise((res) => {
          resolveLoad = res;
        }),
    );
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("loading");
    resolveLoad({ status: "ready", cached: false });
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("ready"),
    );
  });

  it("renders kicanvas-embed with a kicanvas-source child once loaded", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    render(
      <PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} name="blinky" />,
    );
    const root = await waitFor(() => {
      const el = screen.getByTestId("pcb-canvas");
      if (el.dataset.status !== "ready") throw new Error("not ready");
      return el;
    });
    const embed = root.querySelector("kicanvas-embed");
    expect(embed).not.toBeNull();
    expect(embed?.getAttribute("controls")).toBe("full");
    const source = embed?.querySelector("kicanvas-source");
    expect(source).not.toBeNull();
    expect(source?.getAttribute("src")).toBe("/examples/blinky/blinky.kicad_pcb");
    expect(source?.getAttribute("name")).toBe("blinky");
  });

  it("remounts the embed when src changes", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    const { rerender } = render(
      <PcbCanvas src="/examples/a.kicad_pcb" loader={loader} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("ready"),
    );
    const firstEmbed = screen.getByTestId("pcb-canvas").querySelector("kicanvas-embed");
    rerender(<PcbCanvas src="/examples/b.kicad_pcb" loader={loader} />);
    await waitFor(() => {
      const source = screen
        .getByTestId("pcb-canvas")
        .querySelector("kicanvas-source");
      expect(source?.getAttribute("src")).toBe("/examples/b.kicad_pcb");
    });
    const secondEmbed = screen.getByTestId("pcb-canvas").querySelector("kicanvas-embed");
    expect(secondEmbed).not.toBe(firstEmbed);
  });

  it("surfaces loader errors as the error state", async () => {
    const loader = vi.fn().mockRejectedValue(new Error("script 404"));
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("error"),
    );
    expect(screen.getByTestId("pcb-canvas").textContent).toContain("script 404");
  });

  it("honours custom controls + controlslist props", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    render(
      <PcbCanvas
        src="/examples/blinky/blinky.kicad_pcb"
        loader={loader}
        controls="basic"
        controlslist="nodownload nooverlay"
      />,
    );
    const embed = await waitFor(() => {
      const el = screen.getByTestId("pcb-canvas").querySelector("kicanvas-embed");
      if (!el) throw new Error("no embed yet");
      return el;
    });
    expect(embed.getAttribute("controls")).toBe("basic");
    expect(embed.getAttribute("controlslist")).toBe("nodownload nooverlay");
  });

  it("hydrates pcbViewStore.layers from the project store", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    await waitFor(() => {
      expect(usePcbViewStore.getState().layers.length).toBe(4);
    });
    // First copper layer should win as default active.
    expect(usePcbViewStore.getState().activeLayerId).toBe(0);
  });

  it("PgUp / PgDn cycle the active layer through the stack", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    const root = await waitFor(() => {
      const el = screen.getByTestId("pcb-canvas");
      if (el.dataset.status !== "ready") throw new Error("not ready");
      return el;
    });
    // Active starts on layer 0 (F.Cu).
    expect(usePcbViewStore.getState().activeLayerId).toBe(0);
    // PgDn moves to the next layer in declaration order (B.Cu = 31).
    fireEvent.keyDown(root, { key: "PageDown" });
    expect(usePcbViewStore.getState().activeLayerId).toBe(31);
    // PgUp wraps back to F.Cu.
    fireEvent.keyDown(root, { key: "PageUp" });
    expect(usePcbViewStore.getState().activeLayerId).toBe(0);
    // PgUp from the first layer wraps to the last (Edge.Cuts = 44).
    fireEvent.keyDown(root, { key: "PageUp" });
    expect(usePcbViewStore.getState().activeLayerId).toBe(44);
  });

  it("Escape clears the selection set", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
      useSelectionStore.getState().select([
        { kind: "footprint", uuid: "abc" },
        { kind: "track", uuid: "def" },
      ]);
    });
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    const root = await waitFor(() => {
      const el = screen.getByTestId("pcb-canvas");
      if (el.dataset.status !== "ready") throw new Error("not ready");
      return el;
    });
    expect(useSelectionStore.getState().selected).toHaveLength(2);
    fireEvent.keyDown(root, { key: "Escape" });
    expect(useSelectionStore.getState().selected).toHaveLength(0);
  });

  it("renders LayerStack alongside the canvas by default", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("ready"),
    );
    expect(screen.getByTestId("layer-stack")).toBeTruthy();
    // 4 layer rows match the sample project's `pcb.layers`.
    expect(screen.getAllByTestId("layer-row")).toHaveLength(4);
  });

  it("hides LayerStack when showLayerPanel=false", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    act(() => {
      useProjectStore.getState().setProject(sampleProject);
    });
    render(
      <PcbCanvas
        src="/examples/blinky/blinky.kicad_pcb"
        loader={loader}
        showLayerPanel={false}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("ready"),
    );
    expect(screen.queryByTestId("layer-stack")).toBeNull();
  });
});
