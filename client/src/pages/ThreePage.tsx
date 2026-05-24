/**
 * `ThreePage` (M3-T-07) — mounts the `@kiclaude/kithree` `Viewer`
 * against the active project's PCB.
 *
 * Data flow:
 *
 *   useProjectStore → JSON.stringify(pcb)
 *     ──▶ cad.sceneFromPcb (wasm shim of crates/cad scene_from_pcb)
 *     ──▶ kithree.Viewer.loadScene
 *
 * The viewer lives for the lifetime of the page; it gets disposed on
 * unmount and rebuilt on every scene-changing event. We never thrash
 * the GL context — `loadScene` swaps in place.
 *
 * Renders a "no project" banner when no project is loaded so the
 * route is reachable in fresh sessions.
 */

import { useEffect, useRef, useState } from "react";

import { loadKiclaudeWasm } from "../lib/wasm";
import { useProjectStore } from "../stores/projectStore";

interface KithreeViewer {
  mount(container: HTMLElement): void;
  dispose(): void;
  loadScene(scene: unknown): unknown;
  clearScene(): void;
}

interface ViewerFactory {
  new (): KithreeViewer;
}

interface SceneWasm {
  sceneFromPcb(pcbJson: string): string;
}

export interface ThreePageProps {
  /** Test seam — defaults to a dynamic import of `@kiclaude/kithree`. */
  loadViewerCtor?: () => Promise<ViewerFactory>;
  /** Test seam — defaults to the real wasm bundle. */
  wasmLoader?: () => Promise<{ cad: SceneWasm }>;
}

async function defaultLoadViewerCtor(): Promise<ViewerFactory> {
  const mod = await import("@kiclaude/kithree");
  return mod.Viewer as unknown as ViewerFactory;
}

export function ThreePage(props: ThreePageProps = {}) {
  const {
    loadViewerCtor = defaultLoadViewerCtor,
    wasmLoader = loadKiclaudeWasm as () => Promise<{ cad: SceneWasm }>,
  } = props;

  const project = useProjectStore((s) => s.project);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<KithreeViewer | null>(null);
  const [viewerReady, setViewerReady] = useState(false);
  const [wasm, setWasm] = useState<SceneWasm | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Mount the viewer exactly once.
  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;
    void loadViewerCtor().then(
      (Ctor) => {
        if (cancelled) return;
        const viewer = new Ctor();
        viewer.mount(container);
        viewerRef.current = viewer;
        setViewerReady(true);
      },
      (err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      cancelled = true;
      viewerRef.current?.dispose();
      viewerRef.current = null;
    };
  }, [loadViewerCtor]);

  // Load wasm in parallel — no order dependency with viewer mount.
  useEffect(() => {
    let cancelled = false;
    void wasmLoader().then(
      (mod) => {
        if (!cancelled) setWasm(mod.cad);
      },
      (err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [wasmLoader]);

  // Recompute the scene whenever the project, viewer, or wasm changes.
  // Depend on the project reference directly — useMemo on a JSON
  // string would de-dupe two structurally-identical PCBs and miss a
  // store update where only `project.name` changed but the editor
  // wants a fresh render anyway.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewerReady || !viewer || !wasm) return;
    if (!project) {
      viewer.clearScene();
      return;
    }
    try {
      const sceneJson = wasm.sceneFromPcb(JSON.stringify(project.pcb));
      const scene = JSON.parse(sceneJson) as unknown;
      viewer.loadScene(scene);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [project, viewerReady, wasm]);

  return (
    <div data-testid="three-page" data-viewer-ready={viewerReady ? "true" : "false"} style={pageStyle}>
      <header style={headerStyle}>
        <a href="#/" data-testid="three-back-link" style={backLinkStyle}>
          ← Back to editor
        </a>
        <span style={{ flex: 1 }}>3D board viewer</span>
        {project ? (
          <span data-testid="three-project-name" style={{ color: "#9ca3af", fontSize: 12 }}>
            {project.name}
          </span>
        ) : null}
      </header>
      {!project ? (
        <p data-testid="three-empty" style={emptyStyle}>
          Open a project to see its 3D board view.
        </p>
      ) : null}
      {error ? (
        <div data-testid="three-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}
      <div
        ref={containerRef}
        data-testid="three-canvas-container"
        style={canvasContainerStyle}
      />
    </div>
  );
}

const pageStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100vh",
  background: "#0d1018",
  color: "#e2e8f0",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "10px 16px",
  borderBottom: "1px solid #1f2330",
  background: "#161b25",
  fontSize: 13,
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  color: "#cbd5e1",
};

const backLinkStyle: React.CSSProperties = {
  color: "#60a5fa",
  textDecoration: "none",
  fontSize: 12,
  textTransform: "none",
  fontWeight: 400,
};

const emptyStyle: React.CSSProperties = {
  padding: 16,
  color: "#9ca3af",
  fontSize: 13,
  margin: 0,
};

const errorRowStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "rgba(255, 77, 79, 0.15)",
  color: "#ff7875",
  fontSize: 12,
  borderBottom: "1px solid #401b1b",
};

const canvasContainerStyle: React.CSSProperties = {
  flex: 1,
  minHeight: 0,
};
