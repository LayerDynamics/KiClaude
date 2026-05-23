import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { loadKiclaudeWasm } from "../../../lib/wasm";
import { usePcbViewStore } from "../../../stores/pcbViewStore";
import { useProjectStore } from "../../../stores/projectStore";

/** Cardinal copper layers the V hotkey hops between. */
const FRONT_COPPER = "F.Cu";
const BACK_COPPER = "B.Cu";

export type RouteSegment = {
  /** Polyline corners (mm). Layer changes happen at the corner
   *  where a via was dropped — split into multiple segments only at
   *  via points. */
  points_mm: Array<[number, number]>;
  layer: string;
};

export interface RouteToolApi {
  /** Active polyline (mm) the user has clicked into. */
  segments: RouteSegment[];
  /** Live next-point under the cursor (used for the in-flight feedback). */
  cursor_mm: [number, number] | null;
  /** True while the user is mid-draw (at least one anchor placed). */
  drawing: boolean;
  /** Vias placed during this route, in order. */
  vias: Array<{ position_mm: [number, number]; net: string }>;
  /** Net the in-flight route carries. */
  net: string;
  /** Live DRC issues from the wasm `checkDrc` shim — re-evaluated on
   *  every cursor move and anchor placement. Empty until the wasm
   *  module loads. */
  liveIssues: RouteDrcIssue[];
  /** Width the in-flight track uses (defaults to the project's
   *  default net class width). */
  width_mm: number;
  /** Last gateway/wasm error. */
  error: string | null;
  /** Add the cursor coordinate as the next anchor (single click). */
  addCorner: (point_mm: [number, number]) => void;
  /** Update the hover-only cursor position (pointer move). */
  setCursor: (point_mm: [number, number] | null) => void;
  /** Drop a via at the current cursor + switch active layer. */
  dropVia: () => void;
  /** End the route — fires the `ui_track_draw_points` REST call(s). */
  finish: () => Promise<void>;
  /** Cancel without saving (Esc). */
  cancel: () => void;
  /** Set the in-flight track width. */
  setWidth: (mm: number) => void;
  /** Set the in-flight net (typically derived from clicking a pad). */
  setNet: (net: string) => void;
}

export interface RouteDrcIssue {
  severity: "error" | "warning";
  kind: string;
  position_mm: { x: number; y: number };
  layer: string;
  description: string;
  items: string[];
  deficit_mm: number;
}

export interface RouteToolProps {
  projectId: string;
  /** Gateway base URL — defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam — defaults to the real `fetch`. */
  fetcher?: typeof fetch;
  /** Test seam — defaults to the real `loadKiclaudeWasm`. */
  wasmLoader?: () => Promise<{
    cad: { checkDrc: (input: string) => string };
  }>;
  /** Default track width for new routes (mm). Defaults to `0.2`. */
  defaultWidthMm?: number;
  /** Default net class clearance when the project has no override
   *  (mm). Defaults to `0.2`. */
  defaultClearanceMm?: number;
  /** Notify-parent on a successful finish, one per straight-line
   *  segment that was saved. */
  onTrackSaved?: (
    uuid: string,
    points_mm: Array<[number, number]>,
    layer: string,
  ) => void;
  /** Notify-parent on a successful via insertion. */
  onViaSaved?: (
    uuid: string,
    position_mm: [number, number],
    net: string,
  ) => void;
}

/**
 * `useRouteTool` (M2-T-03) — interactive manual track routing.
 *
 * State machine:
 *   - first click anchors the start point
 *   - subsequent clicks add corners
 *   - `V` drops a via at the cursor and switches active copper layer
 *     between F.Cu and B.Cu (matching KiCad pcbnew's V binding)
 *   - double-click finishes the route — POSTs each per-layer segment
 *     to `ui_track_draw_points` and each via to `ui_via_place_xy`
 *   - `Esc` cancels with no save
 *
 * **Live DRC**: on every cursor move and anchor, the hook builds a
 * `DrcInput` containing the in-flight route's segments PLUS every
 * existing track/via/pad from `projectStore.pcb`, then calls the
 * wasm-exported `checkDrc` (added by M2-R-06's wasm shim). Issues
 * involving the in-flight `uuid:"in-flight"` items are returned as
 * `liveIssues` — the caller renders these red over the track.
 */
export function useRouteTool(props: RouteToolProps): RouteToolApi {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    wasmLoader,
    defaultWidthMm = 0.2,
    defaultClearanceMm = 0.2,
    onTrackSaved,
    onViaSaved,
  } = props;

  const project = useProjectStore((s) => s.project);
  const activeLayerId = usePcbViewStore((s) => s.activeLayerId);
  const layers = usePcbViewStore((s) => s.layers);
  const setActiveLayer = usePcbViewStore((s) => s.setActiveLayer);

  const [segments, setSegments] = useState<RouteSegment[]>([]);
  const [cursor, setCursorState] = useState<[number, number] | null>(null);
  const [vias, setVias] = useState<RouteToolApi["vias"]>([]);
  const [net, setNet] = useState("");
  const [width, setWidth] = useState(defaultWidthMm);
  const [error, setError] = useState<string | null>(null);
  const [liveIssues, setLiveIssues] = useState<RouteDrcIssue[]>([]);
  const wasmRef = useRef<{ checkDrc: (s: string) => string } | null>(null);
  const aborter = useRef<AbortController | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  // Map the active layer-id → its KiCad layer name.
  const activeLayerName = useMemo(() => {
    if (activeLayerId == null) return FRONT_COPPER;
    return layers.find((l) => l.id === activeLayerId)?.name ?? FRONT_COPPER;
  }, [activeLayerId, layers]);

  // Load wasm once; surface any failure to the user so the live DRC
  // overlay disabled state is observable rather than silent.
  useEffect(() => {
    let cancelled = false;
    const loader = wasmLoader ?? loadKiclaudeWasm;
    (async () => {
      try {
        const mod = await loader();
        if (!cancelled) {
          wasmRef.current = {
            checkDrc: (s: string) =>
              (mod.cad as { checkDrc?: (s: string) => string }).checkDrc?.(s)
                ?? "[]",
          };
        }
      } catch (err) {
        if (!cancelled) {
          setError(`wasm DRC unavailable: ${err instanceof Error ? err.message : String(err)}`);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [wasmLoader]);

  /** Build the kernel input from project + in-flight route. */
  const buildDrcInput = useCallback(
    (
      activeSegments: RouteSegment[],
      cursorPoint: [number, number] | null,
    ) => {
      const tracks: Array<Record<string, unknown>> = [];
      const drcVias: Array<Record<string, unknown>> = [];
      const pads: Array<Record<string, unknown>> = [];

      const proj = project;
      if (proj) {
        for (const tr of proj.pcb.tracks) {
          const pts = tr.points_mm;
          for (let i = 0; i + 1 < pts.length; i++) {
            tracks.push({
              uuid: tr.uuid,
              net: tr.net,
              // KCIR carries a single layer per track segment — the
              // wasm DRC ingests the same flat shape.
              layer: (tr as { layer?: string }).layer ?? FRONT_COPPER,
              start_mm: { x: pts[i]![0], y: pts[i]![1] },
              end_mm: { x: pts[i + 1]![0], y: pts[i + 1]![1] },
              width_mm: tr.width_mm,
            });
          }
        }
      }
      // In-flight segments: split each polyline into wire-level
      // segments so the kernel sees the same primitive shape.
      for (const seg of activeSegments) {
        const pts = seg.points_mm;
        for (let i = 0; i + 1 < pts.length; i++) {
          tracks.push({
            uuid: "in-flight",
            net: net || "<draft>",
            layer: seg.layer,
            start_mm: { x: pts[i]![0], y: pts[i]![1] },
            end_mm: { x: pts[i + 1]![0], y: pts[i + 1]![1] },
            width_mm: width,
          });
        }
      }
      // Cursor segment: from the last anchor of the active segment
      // to the live cursor position — re-checked on every move.
      if (cursorPoint && activeSegments.length > 0) {
        const last = activeSegments[activeSegments.length - 1]!;
        const lastPt = last.points_mm[last.points_mm.length - 1];
        if (lastPt) {
          tracks.push({
            uuid: "in-flight",
            net: net || "<draft>",
            layer: last.layer,
            start_mm: { x: lastPt[0], y: lastPt[1] },
            end_mm: { x: cursorPoint[0], y: cursorPoint[1] },
            width_mm: width,
          });
        }
      }
      // Vias already placed this draw.
      for (const v of vias) {
        drcVias.push({
          uuid: "in-flight-via",
          net: v.net,
          position_mm: { x: v.position_mm[0], y: v.position_mm[1] },
          layers: [FRONT_COPPER, BACK_COPPER],
          drill_mm: 0.3,
          diameter_mm: 0.6,
        });
      }
      // Foreign-net pads from every footprint.
      if (proj) {
        for (const fp of proj.pcb.footprints) {
          const fpPads = (fp as { pads?: Array<Record<string, unknown>> }).pads;
          if (!fpPads) continue;
          for (const pad of fpPads) {
            const padNet =
              typeof pad.net === "string"
                ? (pad.net as string)
                : "";
            const padCenter = pad.position_mm as
              | [number, number]
              | undefined;
            const padSize = pad.size_mm as [number, number] | undefined;
            if (!padCenter || !padSize) continue;
            pads.push({
              footprint_refdes: fp.refdes,
              number: typeof pad.number === "string" ? pad.number : "1",
              net: padNet,
              center_mm: { x: padCenter[0], y: padCenter[1] },
              size_mm: padSize,
              shape: typeof pad.shape === "string" ? pad.shape : "rect",
              rotation_deg:
                typeof pad.rotation_deg === "number"
                  ? (pad.rotation_deg as number)
                  : 0,
              layers:
                Array.isArray(pad.layers) ? (pad.layers as string[]) : ["*.Cu"],
              drill_mm:
                typeof pad.drill_mm === "number" ? (pad.drill_mm as number) : 0,
            });
          }
        }
      }
      return {
        tracks,
        vias: drcVias,
        pads,
        courtyards: [],
        default_clearance_mm: defaultClearanceMm,
        min_annular_ring_mm: 0.15,
        min_drill_to_copper_mm: 0.25,
        net_class_clearances_mm: {},
        net_to_class: {},
      };
    },
    [defaultClearanceMm, net, project, vias, width],
  );

  /** Re-run live DRC against the current segments + cursor. */
  const refreshLiveDrc = useCallback(
    (
      activeSegments: RouteSegment[],
      cursorPoint: [number, number] | null,
    ) => {
      const wasm = wasmRef.current;
      if (!wasm) {
        setLiveIssues([]);
        return;
      }
      try {
        const input = buildDrcInput(activeSegments, cursorPoint);
        const raw = wasm.checkDrc(JSON.stringify(input));
        const all = JSON.parse(raw) as RouteDrcIssue[];
        // Filter to issues that involve the in-flight items — the
        // wasm shim returns *all* DRC findings; we only highlight
        // ones the active draw is creating or aggravating.
        const filtered = all.filter((iss) =>
          iss.items.includes("in-flight") || iss.items.includes("in-flight-via"),
        );
        setLiveIssues(filtered);
      } catch (err) {
        setError(`live DRC: ${err instanceof Error ? err.message : String(err)}`);
        setLiveIssues([]);
      }
    },
    [buildDrcInput],
  );

  const addCorner = useCallback(
    (point_mm: [number, number]) => {
      setError(null);
      setSegments((prev) => {
        if (prev.length === 0) {
          const next: RouteSegment[] = [{ points_mm: [point_mm], layer: activeLayerName }];
          refreshLiveDrc(next, null);
          return next;
        }
        const last = prev[prev.length - 1]!;
        const next = prev.slice(0, -1).concat({
          ...last,
          points_mm: [...last.points_mm, point_mm],
        });
        refreshLiveDrc(next, null);
        return next;
      });
    },
    [activeLayerName, refreshLiveDrc],
  );

  const setCursor = useCallback(
    (point_mm: [number, number] | null) => {
      setCursorState(point_mm);
      refreshLiveDrc(segments, point_mm);
    },
    [refreshLiveDrc, segments],
  );

  const dropVia = useCallback(() => {
    setError(null);
    const last = segments[segments.length - 1];
    const lastPt = last?.points_mm[last.points_mm.length - 1];
    const point = cursor ?? lastPt;
    if (!point) return; // nothing to drop on
    setVias((prev) => [...prev, { position_mm: point, net: net || "<draft>" }]);
    // Swap to the opposite copper layer.
    const opposite =
      activeLayerName === FRONT_COPPER ? BACK_COPPER : FRONT_COPPER;
    const oppositeId = layers.find((l) => l.name === opposite)?.id;
    if (oppositeId != null) setActiveLayer(oppositeId);
    // Start a new segment on the new layer at the via point so the
    // route continues seamlessly.
    setSegments((prev) => {
      const next: RouteSegment[] = [
        ...prev,
        { points_mm: [point], layer: opposite },
      ];
      refreshLiveDrc(next, cursor);
      return next;
    });
  }, [activeLayerName, cursor, layers, net, refreshLiveDrc, segments, setActiveLayer]);

  const cancel = useCallback(() => {
    setSegments([]);
    setCursorState(null);
    setVias([]);
    setLiveIssues([]);
    setError(null);
    aborter.current?.abort();
  }, []);

  const finish = useCallback(async () => {
    // Need at least one straight segment of ≥2 points.
    const drawable = segments.filter((s) => s.points_mm.length >= 2);
    if (drawable.length === 0 && vias.length === 0) {
      cancel();
      return;
    }
    aborter.current?.abort();
    aborter.current = new AbortController();
    try {
      for (const seg of drawable) {
        const url = `${apiBase}/ui_track_draw_points/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            args: {
              net,
              layer: seg.layer,
              width_mm: width,
              points_mm: seg.points_mm,
            },
          }),
          signal: aborter.current.signal,
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          track_uuid?: string;
          error?: string;
        };
        if (!resp.ok || !body.ok || !body.track_uuid) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        onTrackSaved?.(body.track_uuid, seg.points_mm, seg.layer);
      }
      for (const v of vias) {
        const url = `${apiBase}/ui_via_place_xy/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            args: {
              net: v.net,
              position_mm: v.position_mm,
              drill_mm: 0.3,
              diameter_mm: 0.6,
            },
          }),
          signal: aborter.current.signal,
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          via_uuid?: string;
          error?: string;
        };
        if (!resp.ok || !body.ok || !body.via_uuid) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        onViaSaved?.(body.via_uuid, v.position_mm, v.net);
      }
      setSegments([]);
      setCursorState(null);
      setVias([]);
      setLiveIssues([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [
    apiBase,
    cancel,
    fetchImpl,
    net,
    onTrackSaved,
    onViaSaved,
    projectId,
    segments,
    vias,
    width,
  ]);

  // V / Esc hotkeys. V is bound while drawing; Esc always.
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
      } else if ((e.key === "v" || e.key === "V") && segments.length > 0) {
        e.preventDefault();
        dropVia();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancel, dropVia, segments.length]);

  return {
    segments,
    cursor_mm: cursor,
    drawing: segments.length > 0,
    vias,
    net,
    liveIssues,
    width_mm: width,
    error,
    addCorner,
    setCursor,
    dropVia,
    finish,
    cancel,
    setWidth,
    setNet,
  };
}

export interface RouteToolOverlayProps {
  api: RouteToolApi;
  /** Board-mm → container-pixel transform — must match the
   *  [`EditOverlay`]'s transform so the route lines up with the
   *  selection halos. */
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
 * SVG overlay drawing the in-flight route polyline + cursor preview
 * + via dots + red issue markers from `liveIssues`. Returns null
 * when no draw is active.
 */
export function RouteToolOverlay({
  api,
  transform: tx,
  width,
  height,
}: RouteToolOverlayProps) {
  if (!api.drawing && api.vias.length === 0 && api.liveIssues.length === 0) {
    return null;
  }
  const toPx = (p: [number, number]) => ({
    x: tx.originX + p[0] * tx.scaleX,
    y: tx.originY + p[1] * tx.scaleY,
  });
  const segmentPaths = api.segments.map((seg, segIdx) => {
    if (seg.points_mm.length === 0) return null;
    const d = seg.points_mm
      .map((p, i) => {
        const px = toPx(p);
        return `${i === 0 ? "M" : "L"} ${px.x} ${px.y}`;
      })
      .join(" ");
    return (
      <path
        key={`seg-${segIdx}`}
        d={d}
        stroke={seg.layer === BACK_COPPER ? "#2f73c8" : "#c8362f"}
        strokeWidth={Math.max(2, api.width_mm * tx.scaleX)}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
        data-testid="route-segment"
      />
    );
  });
  const cursorPath = (() => {
    if (!api.cursor_mm || api.segments.length === 0) return null;
    const last = api.segments[api.segments.length - 1]!;
    const lastPt = last.points_mm[last.points_mm.length - 1];
    if (!lastPt) return null;
    const a = toPx(lastPt);
    const b = toPx(api.cursor_mm);
    return (
      <line
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke={last.layer === BACK_COPPER ? "#2f73c8" : "#c8362f"}
        strokeWidth={Math.max(2, api.width_mm * tx.scaleX)}
        strokeDasharray="5 4"
        strokeLinecap="round"
        opacity={0.7}
        data-testid="route-cursor"
      />
    );
  })();
  return (
    <svg
      data-testid="route-tool-overlay"
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        pointerEvents: "none",
      }}
    >
      {segmentPaths}
      {cursorPath}
      {api.vias.map((v, i) => {
        const px = toPx(v.position_mm);
        return (
          <g key={`via-${i}`} data-testid="route-via">
            <circle cx={px.x} cy={px.y} r={6} fill="#888" />
            <circle cx={px.x} cy={px.y} r={2} fill="#000" />
          </g>
        );
      })}
      {api.liveIssues.map((iss, i) => {
        const px = toPx([iss.position_mm.x, iss.position_mm.y]);
        return (
          <circle
            key={`drc-${i}`}
            cx={px.x}
            cy={px.y}
            r={9}
            fill="none"
            stroke={iss.severity === "error" ? "#ff4d4f" : "#f0a500"}
            strokeWidth={2}
            data-testid="route-drc-marker"
          >
            <title>
              {iss.kind}: {iss.description}
            </title>
          </circle>
        );
      })}
    </svg>
  );
}
