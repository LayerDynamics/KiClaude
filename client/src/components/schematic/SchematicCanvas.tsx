import { useEffect, useMemo, useRef, useState } from "react";

import { loadKicanvas, type KicanvasReady } from "../../lib/kicanvas-bridge";

import { SelectionOverlay, type SelectionRect } from "./SelectionOverlay";
import { SnapPreview, type SnapAnchor } from "./SnapPreview";

export interface SchematicCanvasProps {
  /** URL to a `.kicad_sch` served by the kiserver / Vite middleware. */
  src: string;
  /** kicanvas `controls` attribute. Defaults to `"full"`. */
  controls?: "none" | "basic" | "full";
  /** Friendly name shown in the overlay. */
  name?: string;
  /** Test seam — defaults to the real `loadKicanvas`. */
  loader?: (
    opts?: Parameters<typeof loadKicanvas>[0],
  ) => Promise<KicanvasReady>;
  /** Fixed pixel height for the viewport. Defaults to `560`. */
  height?: number;
  /** Optional className for the outer wrapper. */
  className?: string;
  /** Currently selected entity rectangles drawn on top of kicanvas. */
  selection?: SelectionRect[];
  /** Optional snap anchor (e.g. while the user is dragging a symbol). */
  snap?: SnapAnchor | null;
  /** Notify parent when the user rubber-bands a selection. The rect
   *  is in canvas pixel space (not mm) — the parent translates it. */
  onSelectionChange?: (rect: SelectionRect | null) => void;
}

type Status = "loading" | "ready" | "error";

/**
 * The M1-T-01 schematic editor canvas.
 *
 * Wraps `<kicanvas-embed>` (the schematic viewer that ships with the
 * M0 PCB bundle) and stacks two transparent overlays on top so the
 * editor can show selection rectangles + a snap preview without
 * teaching kicanvas's WebGL pipeline new tricks. The overlays are
 * pure SVG so they cost nothing when there's nothing to draw —
 * relevant for hitting the M1 NFR-002 ≥60 FPS bar on a 200-symbol
 * sheet (kicanvas handles the heavy frame draws).
 */
export function SchematicCanvas(props: SchematicCanvasProps) {
  const {
    src,
    controls = "full",
    name,
    loader,
    height = 560,
    className,
    selection = [],
    snap = null,
    onSelectionChange,
  } = props;

  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Rubber-band selection state (in container-relative pixel space).
  const [rubber, setRubber] = useState<SelectionRect | null>(null);
  const rubberStart = useRef<{ x: number; y: number } | null>(null);

  // The kicanvas load is memoised via `loadKicanvas`, so consecutive
  // mounts (e.g. when `src` changes) only refire the `useEffect` and
  // not the underlying script-tag injection.
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
      <div
        data-testid="schematic-canvas"
        data-status="error"
        role="alert"
        className={className}
        style={errorStyle}
      >
        kicanvas failed to load: {error}
      </div>
    );
  }
  if (status === "loading") {
    return (
      <div
        data-testid="schematic-canvas"
        data-status="loading"
        className={className}
        style={{ ...wrapperStyle(height), color: "#9ca3af" }}
      >
        loading kicanvas…
      </div>
    );
  }

  return (
    <div
      data-testid="schematic-canvas"
      data-status="ready"
      ref={containerRef}
      className={className}
      style={wrapperStyle(height)}
      onPointerDown={(e) => beginRubberBand(e, containerRef, rubberStart, setRubber)}
      onPointerMove={(e) =>
        updateRubberBand(e, containerRef, rubberStart, setRubber)
      }
      onPointerUp={(e) =>
        endRubberBand(e, containerRef, rubberStart, setRubber, onSelectionChange)
      }
      onPointerCancel={() => {
        rubberStart.current = null;
        setRubber(null);
      }}
    >
      <kicanvas-embed
        key={src}
        controls={controls}
        data-testid="schematic-kicanvas-embed"
        style={kicanvasStyle}
      >
        <kicanvas-source
          src={src}
          type="schematic"
          {...(name ? { name } : {})}
          data-testid="schematic-kicanvas-source"
        />
      </kicanvas-embed>
      <SelectionOverlay
        rects={selection}
        rubber={rubber}
        height={height}
      />
      <SnapPreview anchor={snap} height={height} />
    </div>
  );
}

function wrapperStyle(height: number): React.CSSProperties {
  return {
    position: "relative",
    width: "100%",
    height,
    overflow: "hidden",
    touchAction: "none",
  };
}

const kicanvasStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  height: "100%",
};

const errorStyle: React.CSSProperties = {
  color: "tomato",
  padding: 12,
  border: "1px solid #4a1f1f",
  borderRadius: 4,
};

// ----------------------------------------------------------------
// Rubber-band selection plumbing. Kept in this file (not extracted)
// so the SchematicCanvas can be unit-tested as one component.
// ----------------------------------------------------------------

function beginRubberBand(
  e: React.PointerEvent<HTMLDivElement>,
  ref: React.RefObject<HTMLDivElement | null>,
  start: React.MutableRefObject<{ x: number; y: number } | null>,
  setRubber: (r: SelectionRect | null) => void,
): void {
  if (!ref.current) return;
  // Only react to left-button presses originating on the wrapper
  // itself (not on kicanvas's own pan/zoom buttons).
  if (e.button !== 0) return;
  const target = e.target as HTMLElement;
  if (target.tagName !== "DIV" && target.dataset.testid !== "schematic-canvas") return;
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
  setRubber: (r: SelectionRect | null) => void,
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
  setRubber: (r: SelectionRect | null) => void,
  onSelectionChange?: (r: SelectionRect | null) => void,
): void {
  if (!start.current) return;
  const rect = ref.current?.getBoundingClientRect();
  if (rect) {
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const { x: sx, y: sy } = start.current;
    const final: SelectionRect = {
      x: Math.min(sx, cx),
      y: Math.min(sy, cy),
      width: Math.abs(cx - sx),
      height: Math.abs(cy - sy),
    };
    onSelectionChange?.(final.width < 2 && final.height < 2 ? null : final);
  }
  start.current = null;
  setRubber(null);
  ref.current?.releasePointerCapture?.(e.pointerId);
}
