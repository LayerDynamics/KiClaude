import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useProjectStore, type KcirProject } from "../stores/projectStore";

import { ThreePage } from "./ThreePage";

interface KithreeViewer {
  mount(container: HTMLElement): void;
  dispose(): void;
  loadScene(scene: unknown): unknown;
  clearScene(): void;
}

function makeMockViewerCtor() {
  const instances: Array<KithreeViewer & {
    mountedTo: HTMLElement | null;
    loaded: unknown[];
    cleared: number;
    disposed: number;
  }> = [];
  class FakeViewer implements KithreeViewer {
    mountedTo: HTMLElement | null = null;
    loaded: unknown[] = [];
    cleared = 0;
    disposed = 0;
    mount(container: HTMLElement): void {
      this.mountedTo = container;
      const canvas = document.createElement("canvas");
      canvas.dataset.testid = "three-fake-canvas";
      container.appendChild(canvas);
    }
    dispose(): void {
      this.disposed += 1;
      if (this.mountedTo) this.mountedTo.innerHTML = "";
    }
    loadScene(scene: unknown): unknown {
      this.loaded.push(scene);
      return scene;
    }
    clearScene(): void {
      this.cleared += 1;
    }
  }
  // ThreePage calls `new Ctor()`. Push every instance into `instances`
  // so tests can introspect mount/dispose/loadScene counts.
  const Ctor = function ConstructibleViewer(): KithreeViewer {
    const v = new FakeViewer();
    instances.push(v as never);
    return v;
  } as unknown as new () => KithreeViewer;
  return { Ctor, instances };
}

function buildProject(): KcirProject {
  return {
    kcir_version: "0.4",
    name: "blinky",
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
  };
}

describe("ThreePage (M3-T-07)", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders an empty banner when no project is loaded", async () => {
    const { Ctor } = makeMockViewerCtor();
    const wasm = {
      sceneFromPcb: vi.fn(() => JSON.stringify({ board_thickness_mm: 0, board_outline_mm: [], placements: [] })),
    };
    render(
      <ThreePage
        loadViewerCtor={() => Promise.resolve(Ctor)}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("three-page").dataset.viewerReady).toBe("true"),
    );
    expect(screen.queryByTestId("three-empty")).not.toBeNull();
    expect(wasm.sceneFromPcb).not.toHaveBeenCalled();
  });

  it("mounts the kithree Viewer and renders the project's scene", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const { Ctor, instances } = makeMockViewerCtor();
    const sceneJson = JSON.stringify({
      board_thickness_mm: 1.6,
      board_outline_mm: [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 10],
      ],
      placements: [],
    });
    const wasm = { sceneFromPcb: vi.fn(() => sceneJson) };
    render(
      <ThreePage
        loadViewerCtor={() => Promise.resolve(Ctor)}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() => expect(instances.length).toBe(1));
    await waitFor(() => expect(wasm.sceneFromPcb).toHaveBeenCalledTimes(1));
    const viewer = instances[0]!;
    expect(viewer.mountedTo).not.toBeNull();
    expect(viewer.loaded).toHaveLength(1);
    expect(viewer.loaded[0]).toMatchObject({ board_thickness_mm: 1.6 });
    expect(screen.getByTestId("three-project-name").textContent).toBe("blinky");
  });

  it("re-runs sceneFromPcb when the project changes underneath", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const { Ctor, instances } = makeMockViewerCtor();
    const wasm = {
      sceneFromPcb: vi.fn(() => JSON.stringify({ board_thickness_mm: 1.6, board_outline_mm: [], placements: [] })),
    };
    render(
      <ThreePage
        loadViewerCtor={() => Promise.resolve(Ctor)}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() => expect(wasm.sceneFromPcb).toHaveBeenCalledTimes(1));
    act(() => {
      useProjectStore.getState().setProject({
        ...buildProject(),
        name: "blinky-v2",
      });
    });
    await waitFor(() => expect(wasm.sceneFromPcb).toHaveBeenCalledTimes(2));
    expect(instances[0]!.loaded).toHaveLength(2);
  });

  it("clears the scene when the project is unloaded", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const { Ctor, instances } = makeMockViewerCtor();
    const wasm = {
      sceneFromPcb: vi.fn(() => JSON.stringify({ board_thickness_mm: 1.6, board_outline_mm: [], placements: [] })),
    };
    render(
      <ThreePage
        loadViewerCtor={() => Promise.resolve(Ctor)}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() => expect(wasm.sceneFromPcb).toHaveBeenCalledTimes(1));
    act(() => {
      useProjectStore.getState().clear();
    });
    await waitFor(() => expect(instances[0]!.cleared).toBeGreaterThan(0));
  });

  it("surfaces sceneFromPcb errors in a banner without crashing the page", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const { Ctor } = makeMockViewerCtor();
    const wasm = {
      sceneFromPcb: vi.fn(() => {
        throw new Error("malformed Pcb");
      }),
    };
    render(
      <ThreePage
        loadViewerCtor={() => Promise.resolve(Ctor)}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() => expect(screen.queryByTestId("three-error")).not.toBeNull());
    expect(screen.getByTestId("three-error").textContent ?? "").toContain("malformed Pcb");
  });

  it("disposes the viewer on unmount", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const { Ctor, instances } = makeMockViewerCtor();
    const wasm = {
      sceneFromPcb: vi.fn(() => JSON.stringify({ board_thickness_mm: 1.6, board_outline_mm: [], placements: [] })),
    };
    const { unmount } = render(
      <ThreePage
        loadViewerCtor={() => Promise.resolve(Ctor)}
        wasmLoader={() => Promise.resolve({ cad: wasm })}
      />,
    );
    await waitFor(() => expect(instances.length).toBe(1));
    unmount();
    expect(instances[0]!.disposed).toBeGreaterThan(0);
  });
});
