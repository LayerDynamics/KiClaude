import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { loadKiclaudeWasm } from "../../../lib/wasm";
import { usePcbViewStore } from "../../../stores/pcbViewStore";
import { useProjectStore } from "../../../stores/projectStore";

export interface ZoneFillPolygon {
  points: Array<{ x: number; y: number }>;
  holes: Array<Array<{ x: number; y: number }>>;
}

export interface ZoneFillPreview {
  polygons: ZoneFillPolygon[];
  warnings: string[];
}

export interface ZoneToolApi {
  /** Polygon outline vertices in mm. */
  outline_mm: Array<[number, number]>;
  /** Live cursor position used for the next-edge rubber-band. */
  cursor_mm: [number, number] | null;
  /** True after the first click — outline collection in progress. */
  drawing: boolean;
  /** Live fill preview from the wasm `fillZone` shim. Empty until
   *  the outline has at least 3 vertices and the wasm module loads. */
  preview: ZoneFillPreview;
  /** Net the zone connects to. */
  net: string;
  /** Layer name the zone lives on. */
  layer: string;
  /** Clearance (mm) from this zone to foreign-net copper. */
  clearance_mm: number;
  /** Last gateway / wasm error. */
  error: string | null;
  /** Append a vertex to the outline. */
  addVertex: (point_mm: [number, number]) => void;
  /** Update the hover cursor (drives the rubber-band preview). */
  setCursor: (point_mm: [number, number] | null) => void;
  /** Finalise the zone — POSTs `ui_zone_create_polygon`. */
  finish: () => Promise<void>;
  /** Cancel without saving (Esc). */
  cancel: () => void;
  /** Set the net the zone should connect to. */
  setNet: (net: string) => void;
  /** Set the destination layer name. */
  setLayer: (layer: string) => void;
  /** Set the zone-to-foreign clearance (mm). */
  setClearance: (mm: number) => void;
}

export interface ZoneToolProps {
  projectId: string;
  /** Gateway base URL — defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam — defaults to `fetch`. */
  fetcher?: typeof fetch;
  /** Test seam — defaults to `loadKiclaudeWasm`. */
  wasmLoader?: () => Promise<{
    cad: { fillZone: (input: string) => string };
  }>;
  /** Default clearance for new zones (mm). Defaults to `0.2`. */
  defaultClearanceMm?: number;
  /** Default min-thickness (mm). Defaults to KiCad's `0.25`. */
  defaultMinThicknessMm?: number;
  /** Optional notify-parent on a successful save. */
  onZoneSaved?: (uuid: string) => void;
}

/**
 * `useZoneTool` (M2-T-04) — interactive copper-zone outline editor
 * with live fill preview.
 *
 * State machine:
 *   - first click anchors the outline's first vertex
 *   - subsequent clicks append vertices
 *   - double-click (or `Enter`) finishes — POSTs the polygon to
 *     `ui_zone_create_polygon`
 *   - `Esc` cancels with no save
 *   - `Backspace` removes the most recent vertex (mistakes are
 *     common during a complex zone outline)
 *
 * The fill preview runs the **wasm-exported `fillZone`** kernel from
 * M2-R-05 on every cursor move and vertex change, with the existing
 * project footprints flattened into pad-shaped obstacles so the
 * preview honours pad clearances and same-net thermal reliefs. The
 * preview returns one or more polygons (each with holes), drawn by
 * `ZoneToolOverlay` as translucent green fill.
 */
export function useZoneTool(props: ZoneToolProps): ZoneToolApi {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    wasmLoader,
    defaultClearanceMm = 0.2,
    defaultMinThicknessMm = 0.25,
    onZoneSaved,
  } = props;

  const project = useProjectStore((s) => s.project);
  const activeLayerId = usePcbViewStore((s) => s.activeLayerId);
  const layers = usePcbViewStore((s) => s.layers);

  const activeLayerName = useMemo(() => {
    if (activeLayerId == null) return "F.Cu";
    return layers.find((l) => l.id === activeLayerId)?.name ?? "F.Cu";
  }, [activeLayerId, layers]);

  const [outline, setOutline] = useState<Array<[number, number]>>([]);
  const [cursor, setCursorState] = useState<[number, number] | null>(null);
  const [net, setNet] = useState("");
  const [layer, setLayer] = useState<string>(activeLayerName);
  const [clearance, setClearance] = useState(defaultClearanceMm);
  const [preview, setPreview] = useState<ZoneFillPreview>({
    polygons: [],
    warnings: [],
  });
  const [error, setError] = useState<string | null>(null);
  const wasmRef = useRef<{ fillZone: (s: string) => string } | null>(null);
  const aborter = useRef<AbortController | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  // Track the active layer changes so a user who picks a different
  // layer after starting an outline still gets the right destination.
  useEffect(() => {
    setLayer(activeLayerName);
  }, [activeLayerName]);

  useEffect(() => {
    let cancelled = false;
    const loader = wasmLoader ?? loadKiclaudeWasm;
    (async () => {
      try {
        const mod = await loader();
        if (!cancelled) {
          wasmRef.current = {
            fillZone: (s: string) =>
              (mod.cad as { fillZone?: (s: string) => string }).fillZone?.(s)
                ?? `{"polygons":[],"thermal_spokes":[],"warnings":[]}`,
          };
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            `wasm fill preview unavailable: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [wasmLoader]);

  /** Build the [`ZoneFillInput`] (matching crates/cad's serde shape)
   *  from the outline + the project's existing footprints. */
  const buildFillInput = useCallback(
    (
      activeOutline: Array<[number, number]>,
      cursorPoint: [number, number] | null,
    ) => {
      const outlinePts =
        cursorPoint && activeOutline.length > 0
          ? [...activeOutline, cursorPoint]
          : activeOutline;
      if (outlinePts.length < 3) {
        return null;
      }
      const proj = project;
      const obstacles: Array<Record<string, unknown>> = [];
      if (proj) {
        for (const fp of proj.pcb.footprints) {
          const fpPads = (fp as { pads?: Array<Record<string, unknown>> }).pads;
          if (!fpPads) continue;
          const fpPos = (fp.position_mm as [number, number]) ?? [0, 0];
          const fpRot = (fp.rotation_deg as number) ?? 0;
          const fcos = Math.cos((fpRot * Math.PI) / 180);
          const fsin = Math.sin((fpRot * Math.PI) / 180);
          for (const pad of fpPads) {
            const padPos = pad.position_mm as [number, number] | undefined;
            const padSize = pad.size_mm as [number, number] | undefined;
            if (!padPos || !padSize) continue;
            const cx = fpPos[0] + padPos[0] * fcos - padPos[1] * fsin;
            const cy = fpPos[1] + padPos[0] * fsin + padPos[1] * fcos;
            const padNet = typeof pad.net === "string" ? (pad.net as string) : "";
            const sameNet = padNet === net && net !== "";
            const shapeName =
              typeof pad.shape === "string" ? (pad.shape as string) : "rect";
            const geometryShape =
              shapeName === "circle"
                ? { Circle: { radius_mm: Math.min(padSize[0], padSize[1]) / 2 } }
                : shapeName === "oval"
                  ? { Oval: { size_mm: padSize } }
                  : shapeName === "roundrect"
                    ? {
                        RoundRect: {
                          size_mm: padSize,
                          corner_radius_mm:
                            Math.min(padSize[0], padSize[1]) *
                            ((pad.roundrect_rratio as number | undefined) ?? 0.25),
                        },
                      }
                    : { Rect: { size_mm: padSize } };
            const drillRaw = pad.drill_mm;
            const drill_mm =
              Array.isArray(drillRaw) && typeof drillRaw[0] === "number"
                ? (drillRaw[0] as number)
                : typeof drillRaw === "number"
                  ? (drillRaw as number)
                  : 0;
            const padRotation =
              fpRot + ((pad.rotation_deg as number | undefined) ?? 0);
            const geometry = {
              Pad: {
                center: { x: cx, y: cy },
                shape: geometryShape,
                rotation_deg: padRotation,
                drill_mm,
              },
            };
            obstacles.push({
              geometry,
              extra_clearance_mm: 0,
              thermal_relief: sameNet
                ? {
                    gap_mm: 0.5,
                    spoke_width_mm: 0.5,
                    spoke_count: 4,
                    spoke_rotation_deg: drill_mm > 0 ? 45 : 0,
                  }
                : null,
            });
          }
        }
      }
      const outlinePolygon = {
        points: outlinePts.map(([x, y]) => ({ x, y })),
        holes: [],
      };
      return {
        outline: outlinePolygon,
        clearance_mm: clearance,
        min_thickness_mm: defaultMinThicknessMm,
        obstacles,
      };
    },
    [clearance, defaultMinThicknessMm, net, project],
  );

  const refreshPreview = useCallback(
    (
      activeOutline: Array<[number, number]>,
      cursorPoint: [number, number] | null,
    ) => {
      const wasm = wasmRef.current;
      if (!wasm) {
        setPreview({ polygons: [], warnings: [] });
        return;
      }
      const input = buildFillInput(activeOutline, cursorPoint);
      if (!input) {
        setPreview({ polygons: [], warnings: [] });
        return;
      }
      try {
        const raw = wasm.fillZone(JSON.stringify(input));
        const parsed = JSON.parse(raw) as {
          polygons: Array<{
            points: Array<{ x: number; y: number }>;
            holes: Array<Array<{ x: number; y: number }>>;
          }>;
          warnings: string[];
        };
        setPreview({ polygons: parsed.polygons, warnings: parsed.warnings });
      } catch (err) {
        setError(
          `fill preview: ${err instanceof Error ? err.message : String(err)}`,
        );
        setPreview({ polygons: [], warnings: [] });
      }
    },
    [buildFillInput],
  );

  const addVertex = useCallback(
    (point_mm: [number, number]) => {
      setError(null);
      setOutline((prev) => {
        const next = [...prev, point_mm];
        refreshPreview(next, null);
        return next;
      });
    },
    [refreshPreview],
  );

  const setCursor = useCallback(
    (point_mm: [number, number] | null) => {
      setCursorState(point_mm);
      refreshPreview(outline, point_mm);
    },
    [outline, refreshPreview],
  );

  const cancel = useCallback(() => {
    setOutline([]);
    setCursorState(null);
    setPreview({ polygons: [], warnings: [] });
    setError(null);
    aborter.current?.abort();
  }, []);

  const removeLastVertex = useCallback(() => {
    setOutline((prev) => {
      if (prev.length === 0) return prev;
      const next = prev.slice(0, -1);
      refreshPreview(next, cursor);
      return next;
    });
  }, [cursor, refreshPreview]);

  const finish = useCallback(async () => {
    if (outline.length < 3) {
      cancel();
      return;
    }
    aborter.current?.abort();
    aborter.current = new AbortController();
    try {
      const url = `${apiBase}/ui_zone_create_polygon/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          args: {
            net,
            layer,
            clearance_mm: clearance,
            min_thickness_mm: defaultMinThicknessMm,
            outline_mm: outline,
          },
        }),
        signal: aborter.current.signal,
      });
      const body = (await resp.json()) as {
        ok?: boolean;
        zone_uuid?: string;
        error?: string;
      };
      if (!resp.ok || !body.ok || !body.zone_uuid) {
        throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
      }
      onZoneSaved?.(body.zone_uuid);
      cancel();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [
    apiBase,
    cancel,
    clearance,
    defaultMinThicknessMm,
    fetchImpl,
    layer,
    net,
    onZoneSaved,
    outline,
    projectId,
  ]);

  // Esc / Backspace hotkeys.
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
      } else if (e.key === "Backspace" && outline.length > 0) {
        e.preventDefault();
        removeLastVertex();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancel, outline.length, removeLastVertex]);

  return {
    outline_mm: outline,
    cursor_mm: cursor,
    drawing: outline.length > 0,
    preview,
    net,
    layer,
    clearance_mm: clearance,
    error,
    addVertex,
    setCursor,
    finish,
    cancel,
    setNet,
    setLayer,
    setClearance,
  };
}

export interface ZoneToolOverlayProps {
  api: ZoneToolApi;
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
 * SVG overlay: in-flight outline polygon + cursor preview edge + the
 * live fill polygons (translucent green per pour, hatched per hole).
 */
export function ZoneToolOverlay({
  api,
  transform: tx,
  width,
  height,
}: ZoneToolOverlayProps) {
  if (!api.drawing && api.preview.polygons.length === 0) {
    return null;
  }
  const toPx = (p: [number, number]) => ({
    x: tx.originX + p[0] * tx.scaleX,
    y: tx.originY + p[1] * tx.scaleY,
  });
  const toPxObj = (p: { x: number; y: number }) => ({
    x: tx.originX + p.x * tx.scaleX,
    y: tx.originY + p.y * tx.scaleY,
  });
  const outlineD =
    api.outline_mm.length > 0
      ? api.outline_mm
          .map((p, i) => {
            const px = toPx(p);
            return `${i === 0 ? "M" : "L"} ${px.x} ${px.y}`;
          })
          .join(" ")
      : "";
  const cursorEdge = (() => {
    if (!api.cursor_mm || api.outline_mm.length === 0) return null;
    const last = api.outline_mm[api.outline_mm.length - 1]!;
    const a = toPx(last);
    const b = toPx(api.cursor_mm);
    return (
      <line
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke="#48bb78"
        strokeWidth={1}
        strokeDasharray="4 3"
        opacity={0.7}
        data-testid="zone-cursor-edge"
      />
    );
  })();
  return (
    <svg
      data-testid="zone-tool-overlay"
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        pointerEvents: "none",
      }}
    >
      {/* Live fill preview: translucent green per filled polygon. */}
      {api.preview.polygons.map((poly, polyIdx) => {
        const outerD = poly.points
          .map((p, i) => {
            const px = toPxObj(p);
            return `${i === 0 ? "M" : "L"} ${px.x} ${px.y}`;
          })
          .concat("Z")
          .join(" ");
        const holesD = poly.holes
          .map((hole) =>
            hole
              .map((p, i) => {
                const px = toPxObj(p);
                return `${i === 0 ? "M" : "L"} ${px.x} ${px.y}`;
              })
              .concat("Z")
              .join(" "),
          )
          .join(" ");
        return (
          <path
            key={`fill-${polyIdx}`}
            d={`${outerD} ${holesD}`}
            fillRule="evenodd"
            fill="rgba(72, 187, 120, 0.25)"
            stroke="rgba(72, 187, 120, 0.7)"
            strokeWidth={0.5}
            data-testid="zone-fill-polygon"
          />
        );
      })}
      {/* In-flight outline polyline (not yet closed). */}
      {outlineD ? (
        <path
          d={outlineD}
          stroke="#48bb78"
          strokeWidth={1.5}
          fill="none"
          strokeLinejoin="round"
          data-testid="zone-outline-path"
        />
      ) : null}
      {/* Vertex dots so the user can see what they've clicked. */}
      {api.outline_mm.map((p, i) => {
        const px = toPx(p);
        return (
          <circle
            key={`v-${i}`}
            cx={px.x}
            cy={px.y}
            r={3}
            fill="#48bb78"
            data-testid="zone-vertex"
          />
        );
      })}
      {cursorEdge}
    </svg>
  );
}
