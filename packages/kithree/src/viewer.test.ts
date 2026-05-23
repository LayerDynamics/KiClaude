import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// happy-dom has no real WebGL context, so we replace three.js's
// `WebGLRenderer` with a fake that produces a `<canvas>` element and
// records `setSize`/`render`/`dispose` calls. The test below is the
// M0-T-07 integration gate — it verifies the Viewer DOM contract, not
// GL rendering quality.
vi.mock("three", async () => {
  const actual = await vi.importActual<typeof import("three")>("three");
  class FakeWebGLRenderer {
    domElement: HTMLCanvasElement;
    disposed = false;
    setPixelRatio = vi.fn();
    setSize = vi.fn();
    render = vi.fn();
    dispose = vi.fn(() => {
      this.disposed = true;
    });
    constructor() {
      this.domElement = document.createElement("canvas");
    }
  }
  return {
    ...actual,
    WebGLRenderer: FakeWebGLRenderer,
  };
});

// Important: the import has to come AFTER vi.mock so the mocked three.js
// is the one Viewer pulls in.
import { Viewer } from "./viewer.js";

function makeContainer(width = 640, height = 480): HTMLElement {
  const container = document.createElement("div");
  Object.defineProperty(container, "clientWidth", { value: width, configurable: true });
  Object.defineProperty(container, "clientHeight", { value: height, configurable: true });
  document.body.appendChild(container);
  return container;
}

describe("Viewer", () => {
  let viewer: Viewer | null = null;
  let container: HTMLElement | null = null;

  beforeEach(() => {
    container = null;
    viewer = null;
  });

  afterEach(() => {
    viewer?.dispose();
    container?.remove();
  });

  it("mounts a canvas into the container", () => {
    container = makeContainer();
    viewer = new Viewer();
    viewer.mount(container);
    expect(container.querySelector("canvas")).not.toBeNull();
    expect(viewer.domElement).not.toBeNull();
    expect(viewer.domElement?.tagName).toBe("CANVAS");
  });

  it("dispose() removes the canvas and is safe to call twice", () => {
    container = makeContainer();
    viewer = new Viewer();
    viewer.mount(container);
    expect(container.querySelector("canvas")).not.toBeNull();
    viewer.dispose();
    expect(container.querySelector("canvas")).toBeNull();
    viewer.dispose(); // idempotent
    expect(viewer.domElement).toBeNull();
  });

  it("throws on double-mount", () => {
    container = makeContainer();
    viewer = new Viewer();
    viewer.mount(container);
    expect(() => viewer!.mount(container!)).toThrow(/already mounted/);
  });

  it("accepts ViewerOptions overrides", () => {
    container = makeContainer(800, 600);
    viewer = new Viewer({ backgroundColor: "#222222", boardSizeMm: 50, fov: 60 });
    viewer.mount(container);
    expect(viewer.domElement).not.toBeNull();
  });
});
