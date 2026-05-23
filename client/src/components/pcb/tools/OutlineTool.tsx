import { useCallback, useEffect, useRef, useState } from "react";

export type OutlineMode = "rectangle" | "polygon";

export type OutlineRole = "outer" | "cutout";

export interface OutlineToolApi {
  /** Active drawing mode. */
  mode: OutlineMode;
  /** Whether the next ring goes onto the outer outline or a cutout. */
  role: OutlineRole;
  /** Current outer polygon vertices in mm. */
  outer_mm: Array<[number, number]>;
  /** Each cutout's vertices in mm. */
  cutouts_mm: Array<Array<[number, number]>>;
  /** Active polygon mode: vertices captured for the ring being drawn. */
  active_ring_mm: Array<[number, number]>;
  /** Rectangle-mode anchor: first corner of the drag rectangle. */
  rect_anchor_mm: [number, number] | null;
  /** Hover cursor (mm) for rubber-banding the next vertex/corner. */
  cursor_mm: [number, number] | null;
  /** Last gateway error. */
  error: string | null;
  /** True between the first input and finish/cancel. */
  drawing: boolean;
  /** Switch active mode (cancels any in-flight ring). */
  setMode: (mode: OutlineMode) => void;
  /** Switch whether the next ring is the outer outline or a cutout. */
  setRole: (role: OutlineRole) => void;
  /** Pointer-down for rectangle mode (anchors the first corner). */
  beginRectangle: (point_mm: [number, number]) => void;
  /** Pointer-up for rectangle mode (commits the rectangle as a ring). */
  endRectangle: (point_mm: [number, number]) => void;
  /** Append a vertex to the active polygon ring. */
  addPolygonVertex: (point_mm: [number, number]) => void;
  /** Close the active polygon-mode ring and append it to the
   *  outer outline / cutouts list according to `role`. */
  closeActiveRing: () => void;
  /** Update the hover cursor (drives rubber-band). */
  setCursor: (point_mm: [number, number] | null) => void;
  /** Remove the most recent vertex from the active polygon ring. */
  popActiveVertex: () => void;
  /** Save the assembled outer + cutouts via `ui_outline_create_polygon`. */
  finish: () => Promise<void>;
  /** Cancel all in-flight + accumulated rings. */
  cancel: () => void;
  /** Set the stroke width (mm). */
  setStrokeWidth: (mm: number) => void;
  /** Stroke width (mm) — KiCad default is 0.05. */
  stroke_width_mm: number;
}

export interface OutlineToolProps {
  projectId: string;
  /** Gateway base URL — defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam — defaults to `fetch`. */
  fetcher?: typeof fetch;
  /** Default stroke width (mm). */
  defaultStrokeWidthMm?: number;
  /** Notify-parent on a successful save. */
  onOutlineSaved?: (uuid: string) => void;
}

/**
 * `useOutlineTool` (M2-T-05) — interactive board-outline editor.
 *
 * Modes:
 *   - **rectangle** — pointer-down anchors the first corner;
 *     pointer-up commits the rectangle as a 4-vertex CCW ring.
 *   - **polygon** — click-by-click vertex capture; `Enter` /
 *     `Escape` close the ring; `Backspace` pops the last vertex.
 *
 * Each completed ring is assigned to either the outer outline
 * (`role: "outer"`) or to the cutouts list. The tool keeps at most
 * one outer outline — switching role back to `outer` after an outer
 * is set replaces it.
 *
 * `finish()` POSTs the assembled outline (one outer + N cutouts) to
 * `ui_outline_create_polygon`, which persists them as `gr_poly`
 * records on `Edge.Cuts` in the project.
 */
export function useOutlineTool(props: OutlineToolProps): OutlineToolApi {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    defaultStrokeWidthMm = 0.05,
    onOutlineSaved,
  } = props;

  const [mode, setModeState] = useState<OutlineMode>("rectangle");
  const [role, setRoleState] = useState<OutlineRole>("outer");
  const [outer, setOuter] = useState<Array<[number, number]>>([]);
  const [cutouts, setCutouts] = useState<Array<Array<[number, number]>>>([]);
  const [activeRing, setActiveRing] = useState<Array<[number, number]>>([]);
  const [rectAnchor, setRectAnchor] = useState<[number, number] | null>(null);
  const [cursor, setCursorState] = useState<[number, number] | null>(null);
  const [strokeWidth, setStrokeWidth] = useState(defaultStrokeWidthMm);
  const [error, setError] = useState<string | null>(null);
  const aborter = useRef<AbortController | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const setMode = useCallback((next: OutlineMode) => {
    setModeState(next);
    setActiveRing([]);
    setRectAnchor(null);
  }, []);

  const setRole = useCallback((next: OutlineRole) => {
    setRoleState(next);
    setActiveRing([]);
    setRectAnchor(null);
  }, []);

  const commitRing = useCallback(
    (ring: Array<[number, number]>) => {
      if (ring.length < 3) return;
      if (role === "outer") {
        setOuter(ring);
      } else {
        setCutouts((prev) => [...prev, ring]);
      }
    },
    [role],
  );

  const beginRectangle = useCallback(
    (point_mm: [number, number]) => {
      if (mode !== "rectangle") return;
      setError(null);
      setRectAnchor(point_mm);
    },
    [mode],
  );

  const endRectangle = useCallback(
    (point_mm: [number, number]) => {
      if (mode !== "rectangle" || !rectAnchor) return;
      const [ax, ay] = rectAnchor;
      const [bx, by] = point_mm;
      const minX = Math.min(ax, bx);
      const maxX = Math.max(ax, bx);
      const minY = Math.min(ay, by);
      const maxY = Math.max(ay, by);
      // Degenerate rectangle (zero-area drag) — ignore.
      if (maxX - minX < 1e-6 || maxY - minY < 1e-6) {
        setRectAnchor(null);
        return;
      }
      // CCW ring matching KiCad's gr_poly convention.
      const ring: Array<[number, number]> = [
        [minX, minY],
        [maxX, minY],
        [maxX, maxY],
        [minX, maxY],
      ];
      commitRing(ring);
      setRectAnchor(null);
    },
    [commitRing, mode, rectAnchor],
  );

  const addPolygonVertex = useCallback(
    (point_mm: [number, number]) => {
      if (mode !== "polygon") return;
      setError(null);
      setActiveRing((prev) => [...prev, point_mm]);
    },
    [mode],
  );

  const closeActiveRing = useCallback(() => {
    if (mode !== "polygon") return;
    if (activeRing.length < 3) {
      setActiveRing([]);
      return;
    }
    commitRing(activeRing);
    setActiveRing([]);
  }, [activeRing, commitRing, mode]);

  const popActiveVertex = useCallback(() => {
    if (mode !== "polygon") return;
    setActiveRing((prev) => prev.slice(0, -1));
  }, [mode]);

  const setCursor = useCallback((point_mm: [number, number] | null) => {
    setCursorState(point_mm);
  }, []);

  const cancel = useCallback(() => {
    setOuter([]);
    setCutouts([]);
    setActiveRing([]);
    setRectAnchor(null);
    setCursorState(null);
    setError(null);
    aborter.current?.abort();
  }, []);

  const finish = useCallback(async () => {
    if (outer.length < 3) {
      setError("outer outline needs at least 3 vertices before saving");
      return;
    }
    aborter.current?.abort();
    aborter.current = new AbortController();
    try {
      const url = `${apiBase}/ui_outline_create_polygon/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          args: {
            outline_mm: outer,
            cutouts_mm: cutouts,
            stroke_width_mm: strokeWidth,
            layer: "Edge.Cuts",
          },
        }),
        signal: aborter.current.signal,
      });
      const body = (await resp.json()) as {
        ok?: boolean;
        outline_uuid?: string;
        error?: string;
      };
      if (!resp.ok || !body.ok || !body.outline_uuid) {
        throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
      }
      onOutlineSaved?.(body.outline_uuid);
      cancel();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase, cancel, cutouts, fetchImpl, onOutlineSaved, outer, projectId, strokeWidth]);

  // Hotkeys: Enter closes the active polygon ring; Backspace pops a
  // vertex; Esc cancels everything; Tab toggles role outer↔cutout.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        cancel();
      } else if (e.key === "Enter" && mode === "polygon") {
        e.preventDefault();
        closeActiveRing();
      } else if (e.key === "Backspace" && mode === "polygon") {
        e.preventDefault();
        popActiveVertex();
      } else if (e.key === "Tab") {
        e.preventDefault();
        setRoleState((prev) => (prev === "outer" ? "cutout" : "outer"));
        setActiveRing([]);
        setRectAnchor(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancel, closeActiveRing, mode, popActiveVertex]);

  return {
    mode,
    role,
    outer_mm: outer,
    cutouts_mm: cutouts,
    active_ring_mm: activeRing,
    rect_anchor_mm: rectAnchor,
    cursor_mm: cursor,
    error,
    drawing:
      outer.length > 0 ||
      cutouts.length > 0 ||
      activeRing.length > 0 ||
      rectAnchor !== null,
    setMode,
    setRole,
    beginRectangle,
    endRectangle,
    addPolygonVertex,
    closeActiveRing,
    setCursor,
    popActiveVertex,
    finish,
    cancel,
    setStrokeWidth,
    stroke_width_mm: strokeWidth,
  };
}

export interface OutlineToolOverlayProps {
  api: OutlineToolApi;
  transform: {
    scaleX: number;
    scaleY: number;
    originX: number;
    originY: number;
  };
  width: number;
  height: number;
}

/**
 * SVG overlay drawing the outer outline (solid yellow polygon), each
 * cutout (dashed yellow polygon), the active in-flight ring, and the
 * rectangle-mode rubber-band.
 */
export function OutlineToolOverlay({
  api,
  transform: tx,
  width,
  height,
}: OutlineToolOverlayProps) {
  if (!api.drawing && api.cursor_mm == null) {
    return null;
  }
  const toPx = (p: [number, number]) => ({
    x: tx.originX + p[0] * tx.scaleX,
    y: tx.originY + p[1] * tx.scaleY,
  });
  const pathFor = (ring: Array<[number, number]>, closed: boolean) =>
    ring
      .map((p, i) => {
        const px = toPx(p);
        return `${i === 0 ? "M" : "L"} ${px.x} ${px.y}`;
      })
      .concat(closed ? "Z" : "")
      .join(" ");
  const outerPath = api.outer_mm.length >= 3 ? pathFor(api.outer_mm, true) : "";
  const activePath = pathFor(api.active_ring_mm, false);
  const rectPreview = (() => {
    if (api.mode !== "rectangle" || !api.rect_anchor_mm || !api.cursor_mm) {
      return null;
    }
    const a = toPx(api.rect_anchor_mm);
    const c = toPx(api.cursor_mm);
    return (
      <rect
        x={Math.min(a.x, c.x)}
        y={Math.min(a.y, c.y)}
        width={Math.abs(a.x - c.x)}
        height={Math.abs(a.y - c.y)}
        fill="none"
        stroke="#f0e68c"
        strokeWidth={1}
        strokeDasharray="4 3"
        data-testid="outline-rect-preview"
      />
    );
  })();
  return (
    <svg
      data-testid="outline-tool-overlay"
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        pointerEvents: "none",
      }}
    >
      {outerPath ? (
        <path
          d={outerPath}
          stroke="#f0e68c"
          strokeWidth={1.5}
          fill="rgba(240, 230, 140, 0.05)"
          data-testid="outline-outer"
        />
      ) : null}
      {api.cutouts_mm.map((ring, i) => (
        <path
          key={`cut-${i}`}
          d={pathFor(ring, true)}
          stroke="#f0e68c"
          strokeWidth={1.5}
          strokeDasharray="3 2"
          fill="rgba(0,0,0,0.35)"
          data-testid="outline-cutout"
        />
      ))}
      {api.active_ring_mm.length > 0 ? (
        <>
          <path
            d={activePath}
            stroke={api.role === "outer" ? "#f0e68c" : "#f6ad55"}
            strokeWidth={1.5}
            fill="none"
            data-testid="outline-active-ring"
          />
          {api.active_ring_mm.map((p, i) => {
            const px = toPx(p);
            return (
              <circle
                key={`v-${i}`}
                cx={px.x}
                cy={px.y}
                r={3}
                fill={api.role === "outer" ? "#f0e68c" : "#f6ad55"}
                data-testid="outline-vertex"
              />
            );
          })}
          {api.cursor_mm ? (
            <line
              x1={toPx(api.active_ring_mm[api.active_ring_mm.length - 1]!).x}
              y1={toPx(api.active_ring_mm[api.active_ring_mm.length - 1]!).y}
              x2={toPx(api.cursor_mm).x}
              y2={toPx(api.cursor_mm).y}
              stroke={api.role === "outer" ? "#f0e68c" : "#f6ad55"}
              strokeWidth={1}
              strokeDasharray="4 3"
              opacity={0.6}
              data-testid="outline-cursor-edge"
            />
          ) : null}
        </>
      ) : null}
      {rectPreview}
    </svg>
  );
}
