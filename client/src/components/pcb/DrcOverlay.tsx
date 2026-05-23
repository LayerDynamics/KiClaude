import { useCallback, useMemo, useRef, useState } from "react";

import { loadKiclaudeWasm } from "../../lib/wasm";
import { usePcbViewStore } from "../../stores/pcbViewStore";
import { useProjectStore } from "../../stores/projectStore";

export type DrcSeverity = "error" | "warning";

export interface DrcIssue {
  severity: DrcSeverity;
  /** Snake_case kind from `kicad-cli pcb drc` (`clearance`, `courtyard_overlap`, …). */
  type: string;
  layer: string;
  position_mm: { x: number; y: number };
  description: string;
  /** Optional items list (refdes, pad number, uuid). The wasm kernel
   *  always fills this; `kicad-cli` may or may not. */
  items?: string[];
  /** Distance under the rule threshold (mm). Optional — only the
   *  wasm kernel populates it. */
  deficit_mm?: number;
}

export interface DrcRunResult {
  ok: boolean;
  issues: DrcIssue[];
  /** Optional error string from the gateway. */
  error?: string;
  /** Wall-clock the run took, ms. */
  duration_ms: number;
  /** Source — `kicad-cli` is the fab source of truth (M2-P-01,
   *  SPEC §16.1 D8); `wasm` is the live editor-feedback kernel. */
  source: "kicad-cli" | "wasm";
}

export interface DrcOverlayApi {
  /** Most recent results from `kc_drc` via kiconnector. */
  results: DrcRunResult | null;
  /** Live wasm DRC results — refreshed manually via `refreshLive()`. */
  live: DrcRunResult | null;
  /** True while a kicad-cli run is in flight. */
  running: boolean;
  /** Last gateway error from a `run()` call. */
  error: string | null;
  /** The currently-selected issue index in `results.issues`. */
  selectedIndex: number | null;
  /** Trigger a fresh `kc_drc` run via kiconnector. */
  run: () => Promise<DrcRunResult>;
  /** Re-evaluate the live wasm kernel against the current project. */
  refreshLive: () => DrcRunResult;
  /** Clear the most recent results. */
  clear: () => void;
  /** Highlight an issue + fire `onFlyTo` with its position. */
  selectIssue: (index: number | null) => void;
}

export interface DrcOverlayProps {
  /** Path to the `.kicad_pcb` on disk that `kicad-cli` should DRC. */
  pcbPath: string;
  /** Gateway base URL — defaults to `/api/connector` (the
   *  kiconnector mount). */
  apiBase?: string;
  /** Test seam — defaults to `fetch`. */
  fetcher?: typeof fetch;
  /** Test seam — defaults to `loadKiclaudeWasm`. */
  wasmLoader?: () => Promise<{
    cad: { checkDrc: (input: string) => string };
  }>;
  /** Optional fly-to callback fired when the user clicks an issue.
   *  The parent (`PcbCanvas`) updates the camera. */
  onFlyTo?: (position_mm: [number, number], layer: string) => void;
  /** Optional notify-parent on a finished run. */
  onRunComplete?: (result: DrcRunResult) => void;
  /** Default clearance for the wasm kernel (mm). */
  defaultClearanceMm?: number;
}

/**
 * `useDrcOverlay` (M2-T-06) — drives the PCB editor's DRC results
 * panel + on-canvas marker overlay.
 *
 * Two kernels feed it:
 *   - **`kicad-cli`** via `kiconnector /tools/drc` — the fab source of
 *     truth (SPEC §16.1 D8). Triggered by the user clicking "Run
 *     DRC". Results stick around as `results` until the next run.
 *   - **Rust wasm kernel** via the `checkDrc` export — fast, advisory.
 *     Re-evaluated on demand (or on every project change if the
 *     parent wires `refreshLive` to a store subscription). Results
 *     stored in `live` and rendered in a lighter colour by the
 *     overlay so the user can tell which findings are advisory vs
 *     fab-blocking.
 */
export function useDrcOverlay(props: DrcOverlayProps): DrcOverlayApi {
  const {
    pcbPath,
    apiBase = "/api/connector",
    fetcher,
    wasmLoader,
    onFlyTo,
    onRunComplete,
    defaultClearanceMm = 0.2,
  } = props;

  const project = useProjectStore((s) => s.project);
  const [results, setResults] = useState<DrcRunResult | null>(null);
  const [live, setLive] = useState<DrcRunResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const wasmRef = useRef<{ checkDrc: (s: string) => string } | null>(null);
  const aborter = useRef<AbortController | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const ensureWasm = useCallback(async () => {
    if (wasmRef.current) return wasmRef.current;
    const loader = wasmLoader ?? loadKiclaudeWasm;
    const mod = await loader();
    wasmRef.current = {
      checkDrc: (s: string) =>
        (mod.cad as { checkDrc?: (s: string) => string }).checkDrc?.(s) ?? "[]",
    };
    return wasmRef.current;
  }, [wasmLoader]);

  const run = useCallback(async (): Promise<DrcRunResult> => {
    aborter.current?.abort();
    aborter.current = new AbortController();
    setRunning(true);
    setError(null);
    const start = performance.now();
    try {
      const resp = await fetchImpl(`${apiBase}/tools/drc`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ pcb_path: pcbPath }),
        signal: aborter.current.signal,
      });
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      const body = (await resp.json()) as {
        ok?: boolean;
        issues?: Array<Record<string, unknown>>;
        error?: string;
      };
      if (!body.ok) {
        throw new Error(body.error ?? "kc_drc returned ok=false");
      }
      const normalised: DrcIssue[] = (body.issues ?? []).map((raw) => {
        const pos = raw.position_mm as
          | { x?: number; y?: number }
          | undefined;
        return {
          severity:
            raw.severity === "error" || raw.severity === "warning"
              ? raw.severity
              : "warning",
          type: typeof raw.type === "string" ? (raw.type as string) : "unknown",
          layer:
            typeof raw.layer === "string" ? (raw.layer as string) : "any",
          position_mm: {
            x: typeof pos?.x === "number" ? pos.x : 0,
            y: typeof pos?.y === "number" ? pos.y : 0,
          },
          description:
            typeof raw.description === "string"
              ? (raw.description as string)
              : "",
          items: Array.isArray(raw.items)
            ? (raw.items as string[])
            : undefined,
          deficit_mm:
            typeof raw.deficit_mm === "number"
              ? (raw.deficit_mm as number)
              : undefined,
        };
      });
      const result: DrcRunResult = {
        ok: true,
        issues: normalised,
        duration_ms: performance.now() - start,
        source: "kicad-cli",
      };
      setResults(result);
      onRunComplete?.(result);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const fail: DrcRunResult = {
        ok: false,
        issues: [],
        error: message,
        duration_ms: performance.now() - start,
        source: "kicad-cli",
      };
      setResults(fail);
      setError(message);
      return fail;
    } finally {
      setRunning(false);
    }
  }, [apiBase, fetchImpl, onRunComplete, pcbPath]);

  const buildLiveInput = useCallback((): Record<string, unknown> | null => {
    const proj = project;
    if (!proj) return null;
    const tracks: Array<Record<string, unknown>> = [];
    const drcVias: Array<Record<string, unknown>> = [];
    const pads: Array<Record<string, unknown>> = [];
    for (const tr of proj.pcb.tracks) {
      const pts = tr.points_mm;
      for (let i = 0; i + 1 < pts.length; i++) {
        tracks.push({
          uuid: tr.uuid,
          net: tr.net,
          layer: (tr as { layer?: string }).layer ?? "F.Cu",
          start_mm: { x: pts[i]![0], y: pts[i]![1] },
          end_mm: { x: pts[i + 1]![0], y: pts[i + 1]![1] },
          width_mm: tr.width_mm,
        });
      }
    }
    for (const v of proj.pcb.vias as Array<Record<string, unknown>>) {
      const pos = v.position_mm as [number, number] | undefined;
      if (!pos) continue;
      drcVias.push({
        uuid: typeof v.uuid === "string" ? v.uuid : "via",
        net: typeof v.net === "string" ? (v.net as string) : "",
        position_mm: { x: pos[0], y: pos[1] },
        layers: Array.isArray(v.layers)
          ? (v.layers as string[])
          : ["F.Cu", "B.Cu"],
        drill_mm:
          typeof v.drill_mm === "number" ? (v.drill_mm as number) : 0.3,
        diameter_mm:
          typeof v.diameter_mm === "number"
            ? (v.diameter_mm as number)
            : 0.6,
      });
    }
    for (const fp of proj.pcb.footprints) {
      const fpPads = (fp as { pads?: Array<Record<string, unknown>> }).pads;
      if (!fpPads) continue;
      for (const pad of fpPads) {
        const pos = pad.position_mm as [number, number] | undefined;
        const size = pad.size_mm as [number, number] | undefined;
        if (!pos || !size) continue;
        pads.push({
          footprint_refdes: fp.refdes,
          number: typeof pad.number === "string" ? pad.number : "1",
          net: typeof pad.net === "string" ? (pad.net as string) : "",
          center_mm: { x: pos[0], y: pos[1] },
          size_mm: size,
          shape: typeof pad.shape === "string" ? (pad.shape as string) : "rect",
          rotation_deg:
            typeof pad.rotation_deg === "number"
              ? (pad.rotation_deg as number)
              : 0,
          layers: Array.isArray(pad.layers)
            ? (pad.layers as string[])
            : ["*.Cu"],
          drill_mm:
            typeof pad.drill_mm === "number" ? (pad.drill_mm as number) : 0,
        });
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
  }, [defaultClearanceMm, project]);

  const refreshLive = useCallback((): DrcRunResult => {
    const wasm = wasmRef.current;
    const start = performance.now();
    if (!wasm) {
      // Fire-and-forget load; the next `refreshLive` will return real
      // results.
      ensureWasm().catch((err) =>
        setError(
          `wasm DRC unavailable: ${err instanceof Error ? err.message : String(err)}`,
        ),
      );
      const empty: DrcRunResult = {
        ok: false,
        issues: [],
        error: "wasm not loaded",
        duration_ms: 0,
        source: "wasm",
      };
      setLive(empty);
      return empty;
    }
    const input = buildLiveInput();
    if (!input) {
      const empty: DrcRunResult = {
        ok: true,
        issues: [],
        duration_ms: 0,
        source: "wasm",
      };
      setLive(empty);
      return empty;
    }
    try {
      const raw = wasm.checkDrc(JSON.stringify(input));
      const issues = JSON.parse(raw) as Array<{
        severity: string;
        kind: string;
        layer: string;
        position_mm: { x: number; y: number };
        description: string;
        items: string[];
        deficit_mm: number;
      }>;
      const result: DrcRunResult = {
        ok: true,
        issues: issues.map((iss) => ({
          severity: iss.severity === "error" ? "error" : "warning",
          type: iss.kind,
          layer: iss.layer,
          position_mm: iss.position_mm,
          description: iss.description,
          items: iss.items,
          deficit_mm: iss.deficit_mm,
        })),
        duration_ms: performance.now() - start,
        source: "wasm",
      };
      setLive(result);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const fail: DrcRunResult = {
        ok: false,
        issues: [],
        error: message,
        duration_ms: performance.now() - start,
        source: "wasm",
      };
      setLive(fail);
      return fail;
    }
  }, [buildLiveInput, ensureWasm]);

  const clear = useCallback(() => {
    setResults(null);
    setLive(null);
    setSelectedIndex(null);
    setError(null);
  }, []);

  const selectIssue = useCallback(
    (index: number | null) => {
      setSelectedIndex(index);
      if (index == null) return;
      const issue = results?.issues[index];
      if (issue && onFlyTo) {
        onFlyTo([issue.position_mm.x, issue.position_mm.y], issue.layer);
      }
    },
    [onFlyTo, results],
  );

  return {
    results,
    live,
    running,
    error,
    selectedIndex,
    run,
    refreshLive,
    clear,
    selectIssue,
  };
}

export interface DrcOverlayProps2 extends DrcOverlayProps {
  /** Optional className for the side panel container. */
  className?: string;
  /** Whether to render the right-side results panel. */
  showPanel?: boolean;
  /** Canvas mm → pixel transform for the marker overlay. */
  transform?: {
    scaleX: number;
    scaleY: number;
    originX: number;
    originY: number;
  };
  /** Container width + height (px). */
  width?: number;
  height?: number;
}

/**
 * Composite component: instantiates `useDrcOverlay`, renders the
 * results panel (severity-grouped issue list), and stacks an SVG
 * marker overlay on the canvas for both `kicad-cli` (solid) and
 * `wasm` (dim) findings. Clicking an issue selects it and fires
 * `onFlyTo`.
 */
export function DrcOverlay(props: DrcOverlayProps2) {
  const {
    className,
    showPanel = true,
    transform,
    width = 0,
    height = 0,
    ...api
  } = props;
  const drc = useDrcOverlay(api);
  const layers = usePcbViewStore((s) => s.layers);
  const allIssues = useMemo(
    () => ({
      cli: drc.results?.issues ?? [],
      wasm: drc.live?.issues ?? [],
    }),
    [drc.results, drc.live],
  );
  const tx = transform ?? {
    scaleX: 4,
    scaleY: 4,
    originX: width / 2,
    originY: height / 2,
  };
  const toPx = (x: number, y: number) => ({
    x: tx.originX + x * tx.scaleX,
    y: tx.originY + y * tx.scaleY,
  });
  return (
    <>
      {/* Markers — sit on top of the kicanvas embed, beside the
          EditOverlay. Wasm findings dimmed so cli (fab source of
          truth) reads as primary. */}
      <svg
        data-testid="drc-marker-overlay"
        style={{
          position: "absolute",
          inset: 0,
          width,
          height,
          pointerEvents: "none",
        }}
      >
        {allIssues.wasm.map((iss, i) => {
          const px = toPx(iss.position_mm.x, iss.position_mm.y);
          return (
            <circle
              key={`wasm-${i}`}
              cx={px.x}
              cy={px.y}
              r={8}
              fill="none"
              stroke={iss.severity === "error" ? "#ff4d4f" : "#f0a500"}
              strokeWidth={1.5}
              strokeDasharray="3 2"
              opacity={0.5}
              data-testid="drc-marker-wasm"
            >
              <title>
                [advisory] {iss.type}: {iss.description}
              </title>
            </circle>
          );
        })}
        {allIssues.cli.map((iss, i) => {
          const px = toPx(iss.position_mm.x, iss.position_mm.y);
          const selected = i === drc.selectedIndex;
          return (
            <circle
              key={`cli-${i}`}
              cx={px.x}
              cy={px.y}
              r={selected ? 14 : 10}
              fill={selected ? "rgba(255, 77, 79, 0.18)" : "none"}
              stroke={iss.severity === "error" ? "#ff4d4f" : "#f0a500"}
              strokeWidth={selected ? 3 : 2}
              data-testid="drc-marker-cli"
              data-selected={selected ? "true" : "false"}
            >
              <title>
                {iss.type}: {iss.description}
              </title>
            </circle>
          );
        })}
      </svg>
      {showPanel ? (
        <aside
          data-testid="drc-results-panel"
          className={className}
          style={{
            ...panelStyle,
          }}
        >
          <header style={headerStyle}>
            <span style={{ flex: 1 }}>DRC</span>
            <button
              type="button"
              onClick={() => {
                void drc.run();
              }}
              disabled={drc.running}
              data-testid="drc-run-button"
              style={runButtonStyle(drc.running)}
            >
              {drc.running ? "Running…" : "Run DRC"}
            </button>
            <button
              type="button"
              onClick={() => {
                drc.refreshLive();
              }}
              data-testid="drc-refresh-live"
              style={ghostButtonStyle}
            >
              Refresh live
            </button>
          </header>
          {drc.results ? (
            <div style={metaRowStyle} data-testid="drc-meta">
              <span>
                kicad-cli: {allIssues.cli.length} issues (
                {Math.round(drc.results.duration_ms)} ms)
              </span>
              {drc.results.error ? (
                <span style={errorStyle}>error: {drc.results.error}</span>
              ) : null}
            </div>
          ) : null}
          {drc.live ? (
            <div style={metaRowStyle} data-testid="drc-live-meta">
              <span style={{ color: "#9ca3af" }}>
                wasm live: {allIssues.wasm.length} advisory
              </span>
            </div>
          ) : null}
          <ul style={listStyle} role="listbox" aria-label="DRC findings">
            {allIssues.cli.map((iss, i) => {
              const selected = i === drc.selectedIndex;
              const layerName =
                layers.find((l) => l.name === iss.layer)?.name ?? iss.layer;
              return (
                <li
                  key={`cli-row-${i}`}
                  role="option"
                  aria-selected={selected}
                  data-testid="drc-issue-row"
                  data-selected={selected ? "true" : "false"}
                  onClick={() => drc.selectIssue(i)}
                  style={rowStyle(selected, iss.severity)}
                >
                  <span style={severityChipStyle(iss.severity)}>
                    {iss.severity}
                  </span>
                  <span style={{ flex: 1 }}>
                    <strong>{iss.type}</strong>{" "}
                    <span style={{ color: "#9ca3af" }}>{layerName}</span>
                    <div style={{ fontSize: 11, color: "#cbd5e1" }}>
                      {iss.description}
                    </div>
                  </span>
                </li>
              );
            })}
            {allIssues.cli.length === 0 && drc.results ? (
              <li style={emptyRowStyle}>No DRC violations.</li>
            ) : null}
          </ul>
        </aside>
      ) : null}
    </>
  );
}

const panelStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  right: 12,
  width: 280,
  maxHeight: "60%",
  display: "flex",
  flexDirection: "column",
  background: "#10131a",
  border: "1px solid #1f2330",
  borderRadius: 6,
  overflow: "hidden",
  fontSize: 12,
  pointerEvents: "auto",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "8px 12px",
  borderBottom: "1px solid #1f2330",
  fontWeight: 600,
  color: "#cbd5e1",
  letterSpacing: 0.4,
  textTransform: "uppercase",
  background: "#161b25",
};

function runButtonStyle(running: boolean): React.CSSProperties {
  return {
    padding: "4px 8px",
    background: running ? "#1f2937" : "#1e40af",
    color: "#f9fafb",
    border: "none",
    borderRadius: 4,
    cursor: running ? "wait" : "pointer",
    fontSize: 11,
  };
}

const ghostButtonStyle: React.CSSProperties = {
  padding: "4px 8px",
  background: "transparent",
  color: "#9ca3af",
  border: "1px solid #1f2937",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 11,
};

const metaRowStyle: React.CSSProperties = {
  padding: "4px 12px",
  fontSize: 11,
  color: "#9ca3af",
  borderBottom: "1px solid #1a1f2a",
};

const errorStyle: React.CSSProperties = {
  color: "#ff7875",
  marginLeft: 8,
};

const listStyle: React.CSSProperties = {
  margin: 0,
  padding: 0,
  listStyle: "none",
  overflowY: "auto",
  flex: 1,
};

function rowStyle(selected: boolean, severity: DrcSeverity): React.CSSProperties {
  return {
    display: "flex",
    gap: 8,
    padding: "8px 12px",
    borderBottom: "1px solid #1a1f2a",
    background: selected ? "#1d2535" : "transparent",
    cursor: "pointer",
    borderLeft: `3px solid ${severity === "error" ? "#ff4d4f" : "#f0a500"}`,
  };
}

function severityChipStyle(severity: DrcSeverity): React.CSSProperties {
  return {
    background:
      severity === "error" ? "rgba(255, 77, 79, 0.2)" : "rgba(240, 165, 0, 0.2)",
    color: severity === "error" ? "#ff7875" : "#fbbf24",
    padding: "2px 6px",
    borderRadius: 3,
    fontSize: 10,
    textTransform: "uppercase",
    fontWeight: 600,
    height: 18,
  };
}

const emptyRowStyle: React.CSSProperties = {
  padding: "12px",
  color: "#9ca3af",
  textAlign: "center",
};
