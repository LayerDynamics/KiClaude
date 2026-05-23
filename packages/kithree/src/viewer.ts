import {
  AmbientLight,
  AxesHelper,
  Color,
  DirectionalLight,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  WebGLRenderer,
} from "three";

/**
 * Configuration for {@link Viewer}. All fields optional; defaults are
 * tuned for a kiclaude PCB-scale board (100mm × 100mm visible area).
 */
export interface ViewerOptions {
  /** Hex background color. Defaults to a dark slate `#0f172a`. */
  backgroundColor?: string;
  /** Plane size in mm. Defaults to `100`. */
  boardSizeMm?: number;
  /** Camera FOV in degrees. Defaults to `45`. */
  fov?: number;
  /** Width in CSS px. Falls back to `container.clientWidth` if omitted. */
  width?: number;
  /** Height in CSS px. Falls back to `container.clientHeight` if omitted. */
  height?: number;
}

/**
 * Minimal three.js scene for kiclaude's M0 PCB preview. Mounts a single
 * flat board plane with directional + ambient lighting and an axes helper.
 *
 * Production usage:
 *
 * ```ts
 * const viewer = new Viewer({ backgroundColor: "#111827" });
 * viewer.mount(document.getElementById("pcb-3d")!);
 * // ... user interaction ...
 * viewer.dispose();
 * ```
 *
 * The full board geometry pipeline (footprint extrusion, copper layers,
 * solder mask, 3D STEP models) lands in M3 — `Viewer` keeps a stable
 * `mount` / `dispose` surface that downstream callers can rely on.
 */
export class Viewer {
  private readonly opts: Required<Omit<ViewerOptions, "width" | "height">> & {
    width: number | null;
    height: number | null;
  };

  private renderer: WebGLRenderer | null = null;
  private scene: Scene | null = null;
  private camera: PerspectiveCamera | null = null;
  private container: HTMLElement | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private animationFrame: number | null = null;

  constructor(options: ViewerOptions = {}) {
    this.opts = {
      backgroundColor: options.backgroundColor ?? "#0f172a",
      boardSizeMm: options.boardSizeMm ?? 100,
      fov: options.fov ?? 45,
      width: options.width ?? null,
      height: options.height ?? null,
    };
  }

  /**
   * Mount the three.js renderer into `container`. The container's size
   * is read at mount time; subsequent resizes are picked up via a
   * `ResizeObserver` (when available).
   */
  mount(container: HTMLElement): void {
    if (this.renderer) {
      throw new Error("Viewer already mounted — call dispose() first.");
    }
    this.container = container;

    const width = this.opts.width ?? container.clientWidth ?? 640;
    const height = this.opts.height ?? container.clientHeight ?? 480;

    const scene = new Scene();
    scene.background = new Color(this.opts.backgroundColor);

    const camera = new PerspectiveCamera(
      this.opts.fov,
      width / Math.max(height, 1),
      0.1,
      10_000,
    );
    camera.position.set(120, 120, 160);
    camera.lookAt(0, 0, 0);

    // Board plane.
    const plane = new Mesh(
      new PlaneGeometry(this.opts.boardSizeMm, this.opts.boardSizeMm),
      new MeshStandardMaterial({ color: 0x1f6f43, metalness: 0.1, roughness: 0.7 }),
    );
    plane.rotation.x = -Math.PI / 2;
    scene.add(plane);

    // Lighting.
    const ambient = new AmbientLight(0xffffff, 0.4);
    scene.add(ambient);
    const directional = new DirectionalLight(0xffffff, 0.8);
    directional.position.set(50, 80, 100);
    scene.add(directional);

    // Axes helper at the board corner — handy for orientation during
    // M0 debugging; will be hidden behind a toggle once the M3 UI lands.
    scene.add(new AxesHelper(20));

    const renderer = new WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(globalThis.devicePixelRatio || 1);
    renderer.setSize(width, height);
    container.appendChild(renderer.domElement);

    this.renderer = renderer;
    this.scene = scene;
    this.camera = camera;
    this.attachResizeObserver(container);
    this.startRenderLoop();
  }

  /**
   * Tear down the renderer, free GL resources, remove the canvas from
   * the DOM, and disconnect the resize observer. Safe to call before
   * {@link mount} (no-op).
   */
  dispose(): void {
    if (this.animationFrame !== null) {
      cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    }
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
      this.resizeObserver = null;
    }
    if (this.renderer && this.container && this.renderer.domElement.parentNode) {
      this.container.removeChild(this.renderer.domElement);
    }
    this.renderer?.dispose();
    this.renderer = null;
    this.scene = null;
    this.camera = null;
    this.container = null;
  }

  /** The mounted DOM `<canvas>` element, or `null` before mount. */
  get domElement(): HTMLCanvasElement | null {
    return this.renderer?.domElement ?? null;
  }

  private attachResizeObserver(container: HTMLElement): void {
    if (typeof ResizeObserver === "undefined") return;
    this.resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        this.handleResize(width, height);
      }
    });
    this.resizeObserver.observe(container);
  }

  private handleResize(width: number, height: number): void {
    if (!this.renderer || !this.camera) return;
    const safeWidth = Math.max(width, 1);
    const safeHeight = Math.max(height, 1);
    this.renderer.setSize(safeWidth, safeHeight);
    this.camera.aspect = safeWidth / safeHeight;
    this.camera.updateProjectionMatrix();
  }

  private startRenderLoop(): void {
    const tick = (): void => {
      if (!this.renderer || !this.scene || !this.camera) return;
      this.renderer.render(this.scene, this.camera);
      if (typeof requestAnimationFrame !== "undefined") {
        this.animationFrame = requestAnimationFrame(tick);
      }
    };
    tick();
  }
}
