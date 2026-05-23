import { useCallback, useEffect, useRef, useState } from "react";

export interface WireToolApi {
  /** Active wire's pending points (always relative to the canvas). */
  points: Array<[number, number]>;
  /** True while the user is mid-draw. */
  drawing: boolean;
  /** Last error from the gateway. */
  error: string | null;
  /** Add the click coordinate as the next vertex. */
  addPoint: (x: number, y: number) => void;
  /** End the wire after the next vertex (double-click). */
  endWire: () => Promise<void>;
  /** Cancel without saving (Esc). */
  cancel: () => void;
}

export interface WireToolProps {
  projectId: string;
  /** Active sheet uuid the wire belongs to. */
  sheetUuid?: string;
  /** Gateway base path. Defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam. */
  fetcher?: typeof fetch;
  /** Optional callback when the wire is saved. */
  onWireSaved?: (wireUuid: string, points: Array<[number, number]>) => void;
}

/**
 * `useWireTool` (M1-T-03) — interactive wire-draw state machine.
 *
 * Click-to-add vertices, double-click ends the wire, `Esc` cancels.
 * Wires of fewer than 2 points are silently discarded.
 *
 * The returned `addPoint` / `endWire` / `cancel` are stable
 * callbacks the parent canvas wires up to pointer events.
 */
export function useWireTool(props: WireToolProps): WireToolApi {
  const {
    projectId,
    sheetUuid,
    apiBase = "/api/ui",
    fetcher,
    onWireSaved,
  } = props;

  const [points, setPoints] = useState<Array<[number, number]>>([]);
  const [drawing, setDrawing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const addPoint = useCallback((x: number, y: number) => {
    setError(null);
    setDrawing(true);
    setPoints((prev) => [...prev, [x, y]]);
  }, []);

  const cancel = useCallback(() => {
    setPoints([]);
    setDrawing(false);
    setError(null);
  }, []);

  const endWire = useCallback(async () => {
    const finalPoints = points;
    if (finalPoints.length < 2) {
      cancel();
      return;
    }
    try {
      const url = `${apiBase}/ui_wire_draw_points/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          args: {
            sheet_uuid: sheetUuid ?? "",
            points_mm: finalPoints,
          },
        }),
      });
      const body = (await resp.json()) as {
        ok?: boolean;
        wire_uuid?: string;
        error?: string;
      };
      if (!resp.ok || !body.ok || !body.wire_uuid) {
        throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
      }
      onWireSaved?.(body.wire_uuid, finalPoints);
      setPoints([]);
      setDrawing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase, fetchImpl, onWireSaved, points, projectId, sheetUuid, cancel]);

  // Esc cancels the active draw — handy when the wire goes wrong.
  useEffect(() => {
    if (!drawing) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        cancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing, cancel]);

  return { points, drawing, error, addPoint, endWire, cancel };
}

export interface WireToolOverlayProps {
  api: WireToolApi;
  height: number;
}

/**
 * SVG overlay drawing the in-flight wire polyline + dots at each
 * captured vertex. Returns null when no wire is active.
 */
export function WireToolOverlay({ api, height }: WireToolOverlayProps) {
  if (!api.drawing || api.points.length === 0) return null;
  const path = api.points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p[0]} ${p[1]}`)
    .join(" ");
  return (
    <svg
      data-testid="wire-tool-overlay"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height,
        pointerEvents: "none",
      }}
    >
      <path
        d={path}
        stroke="#48bb78"
        strokeWidth={1.5}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
        data-testid="wire-tool-path"
      />
      {api.points.map(([x, y], i) => (
        <circle
          key={`${x}-${y}-${i}`}
          cx={x}
          cy={y}
          r={3}
          fill="#48bb78"
          data-testid="wire-tool-vertex"
        />
      ))}
    </svg>
  );
}

// Suppress an unused-import warning when consumers only need the hook.
const _useRefAnchor = useRef;
void _useRefAnchor;
