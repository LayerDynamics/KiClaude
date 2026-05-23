import { useEffect, useMemo, useRef, useState } from "react";

import {
  loadKicanvas,
  type KicanvasReady,
} from "../../lib/kicanvas-bridge";
import { useProjectStore } from "../../stores/projectStore";
import {
  getLayerView,
  usePcbViewStore,
} from "../../stores/pcbViewStore";
import { useSelectionStore } from "../../stores/selectionStore";
import { Card } from "../UI";

import { EditOverlay, DEFAULT_BOARD_TRANSFORM } from "./EditOverlay";
import { LayerStack } from "./LayerStack";

export interface PcbCanvasProps {
  /** URL to a `.kicad_pcb` (or `.kicad_pro`). */
  src: string;
  /** Pan/zoom + overlay UI level. Defaults to `"full"`. */
  controls?: "none" | "basic" | "full";
  /** Optional list of `controlslist` flags (e.g. `"nodownload nooverlay"`). */
  controlslist?: string;
  /** Friendly name shown in the kicanvas overlay. */
  name?: string;
  /** Test seam: override the kicanvas loader (default is real bridge). */
  loader?: (
    opts?: Parameters<typeof loadKicanvas>[0],
  ) => Promise<KicanvasReady>;
  /** Fixed pixel height for the viewport. Defaults to `480`. */
  height?: number;
  /** Optional className for the outer wrapper. */
  className?: string;
  /** Show the [`LayerStack`] panel alongside the canvas. Defaults
   *  to `true`; turn off for embeds that already have their own
   *  layer UI (e.g. the diff viewer). */
  showLayerPanel?: boolean;
}

type Status = "loading" | "ready" | "error";

/**
 * M2-T-01 PCB editor canvas.
 *
 * Wraps `<kicanvas-embed>` (the read-only WebGL board view) and
 * stacks an [`EditOverlay`] SVG on top so selection halos, hover
 * hints, and the rubber-band rectangle can be drawn without
 * teaching kicanvas's pipeline new tricks. A [`LayerStack`] panel
 * sits to the right, both driven by [`usePcbViewStore`].
 *
 * Layer keyboard hotkeys (`PgUp` / `PgDn`) cycle the active layer
 * through the stack, matching KiCad pcbnew's default binding. Layer
 * visibility / opacity changes route through the same store so the
 * overlay's selection halos dim when their layer is hidden.
 *
 * The kicanvas embed itself does not currently honour the JS-side
 * layer-visibility state — that needs an upstream kicanvas API the
 * vendored fork doesn't expose. The overlay respects the state in
 * the interim; full kicanvas-layer compositing is tracked under the
 * M2-T-08 follow-up (the layer-stack finaliser).
 */
export function PcbCanvas(props: PcbCanvasProps) {
  const {
    src,
    controls = "full",
    controlslist,
    name,
    loader,
    height = 480,
    className,
    showLayerPanel = true,
  } = props;

  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);

  // Rubber-band selection state in container-relative pixels.
  const [rubber, setRubber] = useState<
    { x: number; y: number; width: number; height: number } | null
  >(null);
  const rubberStart = useRef<{ x: number; y: number } | null>(null);

  // Layout: track the overlay's actual rendered width so the SVG
  // sizes itself to match the kicanvas embed (which fills its
  // flex column).
  const [overlayWidth, setOverlayWidth] = useState(0);

  // Sync the project's `pcb.layers` into `pcbViewStore` whenever the
  // project changes. This is the only place that does the projectStore
  // → pcbViewStore handoff; downstream components subscribe to
  // pcbViewStore only.
  const projectLayers = useProjectStore((s) => s.project?.pcb.layers ?? null);
  const projectLayerColors = useProjectStore(
    (s) =>
      (s.project?.pcb as { layer_colors?: Record<string, string> } | undefined)
        ?.layer_colors ?? null,
  );
  const setLayers = usePcbViewStore((s) => s.setLayers);
  const setLayerColors = usePcbViewStore((s) => s.setLayerColors);
  const cycleActiveLayer = usePcbViewStore((s) => s.cycleActiveLayer);
  const clearSelection = useSelectionStore((s) => s.clear);

  useEffect(() => {
    if (!projectLayers) {
      setLayers([]);
      return;
    }
    setLayers(
      projectLayers.map((l) => ({ id: l.id, name: l.name, kind: l.kind })),
    );
  }, [projectLayers, setLayers]);

  useEffect(() => {
    if (!projectLayerColors) {
      setLayerColors({});
      return;
    }
    // Project file carries `{layer_id_string: "#rrggbb"}`; the store
    // wants numeric keys. Convert here so all downstream subscribers
    // see numeric ids exclusively.
    const normalized: Record<number, string> = {};
    for (const [k, v] of Object.entries(projectLayerColors)) {
      const id = Number.parseInt(k, 10);
      if (Number.isFinite(id)) normalized[id] = v;
    }
    setLayerColors(normalized);
  }, [projectLayerColors, setLayerColors]);

  // PgUp/PgDn → cycle active layer. Bind to the container so the
  // hotkeys work whenever the editor has focus. The ref attaches
  // only after the "ready" render swaps from the loading <Card> to
  // the real viewport, so we depend on `status` to re-bind once
  // the node exists.
  useEffect(() => {
    if (status !== "ready") return;
    const node = containerRef.current;
    if (!node) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "PageUp") {
        e.preventDefault();
        cycleActiveLayer(-1);
      } else if (e.key === "PageDown") {
        e.preventDefault();
        cycleActiveLayer(1);
      } else if (e.key === "Escape") {
        e.preventDefault();
        clearSelection();
      }
    };
    node.addEventListener("keydown", handler);
    return () => node.removeEventListener("keydown", handler);
  }, [status, cycleActiveLayer, clearSelection]);

  // Watch the overlay container's actual rendered width.
  useEffect(() => {
    const node = overlayRef.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setOverlayWidth(entry.contentRect.width);
      }
    });
    observer.observe(node);
    setOverlayWidth(node.clientWidth);
    return () => observer.disconnect();
  }, [status]);

  // The kicanvas load is memoised via `loadKicanvas`, so consecutive
  // mounts (e.g. `src` changes) only refire the effect.
  const load = useMemo(() => loader ?? loadKicanvas, [loader]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setError(null);
    (async () => {
      try {
        await load();
        if (cancelled) return;
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load, src]);

  if (status === "error") {
    return (
      <Card
        tone="danger"
        flush
        data-testid="pcb-canvas"
        data-status="error"
        role="alert"
        className={className}
      >
        <div className="p-3 text-sm text-red-700 dark:text-red-300">
          kicanvas failed to load: {error}
        </div>
      </Card>
    );
  }

  if (status === "loading") {
    return (
      <Card
        tone="muted"
        flush
        data-testid="pcb-canvas"
        data-status="loading"
        className={className}
      >
        <div
          className="flex items-center justify-center text-sm text-[var(--text)]/70"
          style={{ height }}
        >
          loading kicanvas…
        </div>
      </Card>
    );
  }

  return (
    <div
      data-testid="pcb-canvas"
      data-status="ready"
      ref={containerRef}
      tabIndex={0}
      style={{
        display: "flex",
        gap: 8,
        height,
        width: "100%",
        outline: "none",
      }}
      className={className}
    >
      <div
        ref={overlayRef}
        data-testid="pcb-canvas-viewport"
        style={{
          position: "relative",
          flex: 1,
          height: "100%",
          overflow: "hidden",
          touchAction: "none",
          background: "#000",
        }}
        onPointerDown={(e) =>
          beginRubberBand(e, overlayRef, rubberStart, setRubber)
        }
        onPointerMove={(e) =>
          updateRubberBand(e, overlayRef, rubberStart, setRubber)
        }
        onPointerUp={(e) =>
          endRubberBand(e, overlayRef, rubberStart, setRubber)
        }
        onPointerCancel={() => {
          rubberStart.current = null;
          setRubber(null);
        }}
      >
        <kicanvas-embed
          key={src}
          controls={controls}
          controlslist={controlslist}
          data-testid="kicanvas-embed"
          style={{ display: "block", width: "100%", height: "100%" }}
        >
          <kicanvas-source
            src={src}
            {...(name ? { name } : {})}
            data-testid="kicanvas-source"
          />
        </kicanvas-embed>
        <LayerVisibilityScrim />
        <EditOverlay
          width={overlayWidth}
          height={height}
          rubber={rubber}
          transform={DEFAULT_BOARD_TRANSFORM(overlayWidth, height)}
        />
      </div>
      {showLayerPanel ? (
        <LayerStack className="pcb-layer-stack" height={height} />
      ) : null}
    </div>
  );
}

/**
 * Faint scrim that dims the kicanvas embed proportional to the
 * average opacity of currently-hidden layers. Since kicanvas
 * doesn't yet expose per-layer JS controls, this acts as the
 * user-feedback channel — without it the visibility toggles would
 * look like no-ops to a viewer who isn't watching the overlay.
 *
 * The dim level is `1 - mean(visible layers' opacity)` clamped to
 * `[0, 0.6]` so we never fully blackout the viewport.
 */
function LayerVisibilityScrim() {
  const layers = usePcbViewStore((s) => s.layers);
  const layerView = usePcbViewStore((s) => s.layerView);

  if (layers.length === 0) return null;
  let totalVisibleOpacity = 0;
  let visibleCount = 0;
  for (const layer of layers) {
    const view = getLayerView({ layerView }, layer.id);
    if (view.visible) {
      totalVisibleOpacity += view.opacity;
      visibleCount += 1;
    }
  }
  const meanOpacity =
    visibleCount > 0 ? totalVisibleOpacity / visibleCount : 0;
  const dim = Math.min(0.6, Math.max(0, 1 - meanOpacity));
  if (dim < 0.01) return null;
  return (
    <div
      data-testid="layer-visibility-scrim"
      style={{
        position: "absolute",
        inset: 0,
        background: `rgba(0, 0, 0, ${dim.toFixed(3)})`,
        pointerEvents: "none",
      }}
    />
  );
}

// ----------------------------------------------------------------
// Rubber-band selection plumbing — same pattern as SchematicCanvas.
// ----------------------------------------------------------------

function beginRubberBand(
  e: React.PointerEvent<HTMLDivElement>,
  ref: React.RefObject<HTMLDivElement | null>,
  start: React.MutableRefObject<{ x: number; y: number } | null>,
  setRubber: (
    r: { x: number; y: number; width: number; height: number } | null,
  ) => void,
): void {
  if (!ref.current) return;
  if (e.button !== 0) return;
  const target = e.target as HTMLElement;
  // Only start a rubber-band on the bare viewport surface — not on
  // the kicanvas embed's own controls.
  if (target.tagName !== "DIV" && target.dataset.testid !== "pcb-canvas-viewport") {
    return;
  }
  const rect = ref.current.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  start.current = { x, y };
  setRubber({ x, y, width: 0, height: 0 });
  ref.current.setPointerCapture?.(e.pointerId);
}

function updateRubberBand(
  e: React.PointerEvent<HTMLDivElement>,
  ref: React.RefObject<HTMLDivElement | null>,
  start: React.MutableRefObject<{ x: number; y: number } | null>,
  setRubber: (
    r: { x: number; y: number; width: number; height: number } | null,
  ) => void,
): void {
  if (!ref.current || !start.current) return;
  const rect = ref.current.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  const { x: sx, y: sy } = start.current;
  setRubber({
    x: Math.min(sx, cx),
    y: Math.min(sy, cy),
    width: Math.abs(cx - sx),
    height: Math.abs(cy - sy),
  });
}

function endRubberBand(
  e: React.PointerEvent<HTMLDivElement>,
  ref: React.RefObject<HTMLDivElement | null>,
  start: React.MutableRefObject<{ x: number; y: number } | null>,
  setRubber: (
    r: { x: number; y: number; width: number; height: number } | null,
  ) => void,
): void {
  start.current = null;
  setRubber(null);
  ref.current?.releasePointerCapture?.(e.pointerId);
}
