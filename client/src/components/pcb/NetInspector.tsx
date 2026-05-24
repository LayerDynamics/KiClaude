/**
 * `NetInspector` (M3-T-02) — per-net signal-integrity panel.
 *
 * For the currently selected net, computes the live `Z0` of its trace
 * against the project's stackup using the Hammerstad-Jensen +
 * IPC-2141A microstrip / IPC stripline solvers that live in
 * `crates/cad/src/impedance.rs` and ship to the browser via
 * `wasm-pack build --target web crates/cad`.
 *
 * The panel does NOT mutate the persisted KCIR until the user clicks
 * Apply on a "snap" — slider tweaks are local "what-if" exploration.
 * On Apply the panel POSTs `ui_netclass_set` with the snapped trace
 * width to the gateway, which propagates through kiserver into the
 * `.kicad_pcb` net-class entry.
 *
 * Layer-side detection:
 *   - The selected net's first track gives the home layer (e.g.
 *     `F.Cu`, `In1.Cu`, `B.Cu`).
 *   - F.Cu / B.Cu → outer-layer microstrip. The relevant dielectric
 *     is the first dielectric layer found in the stackup moving
 *     "inward" from the home layer.
 *   - Inner copper layers → stripline. The relevant `H` is the
 *     distance to the nearest copper plane (taken as the inner
 *     dielectric thickness; we use the closer of the two adjacent
 *     dielectrics).
 *
 * If the project has no stackup or no tracks for the net, the panel
 * falls back to the project's default trace width and a placeholder
 * stackup (0.15mm FR-4) and labels the result as "no stackup".
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { loadKiclaudeWasm } from "../../lib/wasm";
import type {
  KcirProject,
  KcirStackup,
  KcirStackupLayer,
} from "../../stores/projectStore";
import { useProjectStore } from "../../stores/projectStore";

interface MicrostripWasm {
  microstripZ0(json: string): string;
  striplineZ0(json: string): number;
  differentialMicrostripZ(json: string): string;
  solveMicrostripWidthForZ0(target: number, h: number, er: number, t: number): number;
}

export interface NetInspectorProps {
  projectId: string;
  /** Gateway base URL — defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam — defaults to `globalThis.fetch`. */
  fetcher?: typeof fetch;
  /** Test seam — defaults to `loadKiclaudeWasm()`. */
  wasmLoader?: () => Promise<{ cad: MicrostripWasm }>;
  /** When set, this net is selected on mount. Otherwise the first net wins. */
  initialNet?: string;
  className?: string;
  onApplied?: (payload: { net: string; trace_width_mm: number; class_name: string }) => void;
}

interface ProjectNet {
  name: string;
  power_rail?: string | null;
}

interface ProjectTrack {
  net: string;
  width_mm: number;
  // KiCad tracks carry their home layer in the segment metadata;
  // current KCIR client shape doesn't surface it as a typed field, so
  // we read it dynamically below.
}

interface NetClassRow {
  name: string;
  clearance_mm: number;
  trace_width_mm: number;
}

interface Z0Result {
  hammerstad: number;
  ipc2141: number;
  stripline: number | null;
}

/** Default stackup if the project carries none — keeps the panel useful
 * on M0 fixtures that pre-date M3-R-01. */
const FALLBACK_STACKUP: KcirStackup = {
  layers: [
    { name: "F.Cu", kind: "copper", thickness_mm: 0.035, dielectric_constant: null, loss_tangent: null, color: "copper" },
    { name: "dielectric 1", kind: "dielectric", thickness_mm: 1.51, dielectric_constant: 4.5, loss_tangent: 0.02, color: "FR4" },
    { name: "B.Cu", kind: "copper", thickness_mm: 0.035, dielectric_constant: null, loss_tangent: null, color: "copper" },
  ],
  power_plane_layers: [],
  controlled_impedance: false,
  board_thickness_mm: 1.58,
  finish: "HASL",
};

export interface ResolvedStackup {
  /** "microstrip" when the home layer is on an outer copper (F.Cu /
   * B.Cu), "stripline" for inner-layer routing. */
  mode: "microstrip" | "stripline";
  /** Dielectric height between the trace and the nearest reference
   * plane (mm). */
  height_mm: number;
  /** Effective εr of the adjacent dielectric. */
  dielectric_constant: number;
  /** Copper thickness for the home layer (mm). */
  copper_thickness_mm: number;
  /** Source descriptor — surfaced in the UI so the user knows whether
   * the values came from a real stackup or the fallback. */
  source: "project" | "fallback";
}

/** Find the home-layer index in the stackup. Returns `null` if the
 * layer isn't in the stackup at all. */
function findLayerIndex(stackup: KcirStackup, name: string): number | null {
  const idx = stackup.layers.findIndex((l) => l.name === name);
  return idx >= 0 ? idx : null;
}

function findAdjacentDielectric(
  stackup: KcirStackup,
  homeIdx: number,
  direction: -1 | 1,
): KcirStackupLayer | null {
  for (let i = homeIdx + direction; i >= 0 && i < stackup.layers.length; i += direction) {
    const layer = stackup.layers[i]!;
    if (layer.kind === "dielectric") return layer;
    // Bail on the next copper plane — that's the ground reference for
    // a stripline; no dielectric between the trace and the reference.
    if (layer.kind === "copper") return null;
  }
  return null;
}

export function resolveStackupForLayer(
  stackup: KcirStackup,
  layerName: string,
): ResolvedStackup {
  const homeIdx = findLayerIndex(stackup, layerName);
  // No stackup or unknown layer → microstrip on the fallback geometry.
  if (homeIdx == null) {
    const di = stackup.layers.find((l) => l.kind === "dielectric");
    return {
      mode: "microstrip",
      height_mm: di?.thickness_mm ?? 0.15,
      dielectric_constant: di?.dielectric_constant ?? 4.5,
      copper_thickness_mm: 0.035,
      source: stackup === FALLBACK_STACKUP ? "fallback" : "project",
    };
  }
  const home = stackup.layers[homeIdx]!;
  const copperLayers = stackup.layers
    .map((l, i) => ({ l, i }))
    .filter((p) => p.l.kind === "copper");
  const isOuter =
    copperLayers.length > 0 &&
    (homeIdx === copperLayers[0]!.i ||
      homeIdx === copperLayers[copperLayers.length - 1]!.i);
  if (isOuter) {
    // Microstrip — find the dielectric on the inward side.
    const inward = homeIdx === copperLayers[0]!.i ? 1 : -1;
    const di = findAdjacentDielectric(stackup, homeIdx, inward);
    return {
      mode: "microstrip",
      height_mm: di?.thickness_mm ?? 0.15,
      dielectric_constant: di?.dielectric_constant ?? 4.5,
      copper_thickness_mm: home.thickness_mm || 0.035,
      source: stackup === FALLBACK_STACKUP ? "fallback" : "project",
    };
  }
  // Inner layer → stripline. Pick the thinner of the two adjacent
  // dielectrics as the dominant H (the trace couples most strongly
  // to the nearest reference plane).
  const up = findAdjacentDielectric(stackup, homeIdx, -1);
  const down = findAdjacentDielectric(stackup, homeIdx, 1);
  const di =
    up && down
      ? up.thickness_mm <= down.thickness_mm
        ? up
        : down
      : (up ?? down ?? null);
  return {
    mode: "stripline",
    height_mm: di?.thickness_mm ?? 0.15,
    dielectric_constant: di?.dielectric_constant ?? 4.5,
    copper_thickness_mm: home.thickness_mm || 0.018,
    source: stackup === FALLBACK_STACKUP ? "fallback" : "project",
  };
}

/** First track for a given net — used to discover the net's home
 * layer. The PCB shape in `KcirPcb` doesn't expose layer per track at
 * the type level (kept loose because tracks predate the editor's
 * layer-aware features), so we cast through a structural lookup. */
function findNetHomeLayer(project: KcirProject | null, netName: string): string {
  if (!project) return "F.Cu";
  const tracks = (project.pcb.tracks ?? []) as Array<
    ProjectTrack & { layer?: string }
  >;
  const t = tracks.find((row) => row.net === netName);
  return t?.layer ?? "F.Cu";
}

function findNetClassForNet(project: KcirProject | null, netName: string): string {
  if (!project) return "Default";
  const nets = (project.pcb.nets ?? []) as Array<
    ProjectNet & { class?: string | string[] | null }
  >;
  const n = nets.find((row) => row.name === netName);
  const raw = n?.class;
  if (Array.isArray(raw)) return raw[0] ?? "Default";
  if (typeof raw === "string") return raw;
  return "Default";
}

/** "Snap to 50Ω" / "Snap to 100Ω" targets — single-ended; the diff-
 * pair UI in M3-T-03 will add 90 Ω / 100 Ω differential snaps. */
const SNAP_TARGETS_OHMS = [50, 75, 90, 100];

export function NetInspector(props: NetInspectorProps) {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    wasmLoader = loadKiclaudeWasm as () => Promise<{ cad: MicrostripWasm }>,
    initialNet,
    className,
    onApplied,
  } = props;

  const project = useProjectStore((s) => s.project);
  const nets = useMemo<ProjectNet[]>(() => project?.pcb.nets ?? [], [project]);
  const netClasses = useMemo<NetClassRow[]>(
    () => (project?.net_classes ?? []) as NetClassRow[],
    [project],
  );
  const stackup = project?.stackup ?? FALLBACK_STACKUP;
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const [selectedNet, setSelectedNet] = useState<string>(
    initialNet ?? nets[0]?.name ?? "",
  );
  useEffect(() => {
    if (selectedNet === "" && nets[0]) setSelectedNet(nets[0].name);
  }, [nets, selectedNet]);
  useEffect(() => {
    if (initialNet) setSelectedNet(initialNet);
  }, [initialNet]);

  const homeLayer = useMemo(
    () => findNetHomeLayer(project, selectedNet),
    [project, selectedNet],
  );
  const className_ = useMemo(
    () => findNetClassForNet(project, selectedNet),
    [project, selectedNet],
  );
  const baseClass = useMemo(
    () => netClasses.find((c) => c.name === className_) ?? netClasses[0],
    [netClasses, className_],
  );
  const persistedWidth = baseClass?.trace_width_mm ?? 0.25;

  const resolved = useMemo(
    () => resolveStackupForLayer(stackup, homeLayer),
    [stackup, homeLayer],
  );

  // Working-copy width — driven by the slider and the snap buttons.
  // Reset to the persisted width whenever the selection changes.
  const [width, setWidth] = useState<number>(persistedWidth);
  useEffect(() => {
    setWidth(persistedWidth);
  }, [persistedWidth, selectedNet]);

  const [wasm, setWasm] = useState<MicrostripWasm | null>(null);
  const [wasmError, setWasmError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void wasmLoader().then(
      (mod) => {
        if (!cancelled) setWasm(mod.cad);
      },
      (err: unknown) => {
        if (!cancelled) {
          setWasmError(err instanceof Error ? err.message : String(err));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [wasmLoader]);

  const z0: Z0Result | null = useMemo(() => {
    if (!wasm) return null;
    const geom = {
      width_mm: width,
      thickness_mm: resolved.copper_thickness_mm,
      dielectric_height_mm: resolved.height_mm,
      dielectric_constant: resolved.dielectric_constant,
    };
    try {
      const microRaw = wasm.microstripZ0(JSON.stringify(geom));
      const micro = JSON.parse(microRaw) as {
        z0_hammerstad_ohms: number;
        z0_ipc2141_ohms: number;
      };
      const stripline =
        resolved.mode === "stripline" ? wasm.striplineZ0(JSON.stringify(geom)) : null;
      return {
        hammerstad: micro.z0_hammerstad_ohms,
        ipc2141: micro.z0_ipc2141_ohms,
        stripline,
      };
    } catch (err) {
      setWasmError(err instanceof Error ? err.message : String(err));
      return null;
    }
  }, [wasm, width, resolved]);

  const primaryZ0 = useMemo(() => {
    if (!z0) return null;
    return resolved.mode === "stripline" ? z0.stripline : z0.hammerstad;
  }, [z0, resolved]);

  const snap = useCallback(
    (targetOhms: number) => {
      if (!wasm) return;
      const w = wasm.solveMicrostripWidthForZ0(
        targetOhms,
        resolved.height_mm,
        resolved.dielectric_constant,
        resolved.copper_thickness_mm,
      );
      if (!Number.isFinite(w)) {
        setWasmError(
          `target ${targetOhms} Ω unreachable on this stackup (h=${resolved.height_mm.toFixed(3)} mm, εr=${resolved.dielectric_constant})`,
        );
        return;
      }
      setWasmError(null);
      setWidth(Number.parseFloat(w.toFixed(4)));
    },
    [wasm, resolved],
  );

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(async () => {
    if (!baseClass) {
      setError("no net class to update");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const url = `${apiBase}/ui_netclass_set/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          args: {
            name: baseClass.name,
            trace_width_mm: width,
            clearance_mm: baseClass.clearance_mm,
            bind_nets: [selectedNet],
          },
        }),
      });
      const body = (await resp.json()) as {
        ok?: boolean;
        error?: string;
        detail?: string;
      };
      if (!resp.ok || !body.ok) {
        throw new Error(body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`);
      }
      onApplied?.({
        net: selectedNet,
        trace_width_mm: width,
        class_name: baseClass.name,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [apiBase, baseClass, fetchImpl, onApplied, projectId, selectedNet, width]);

  if (!project) {
    return (
      <div data-testid="net-inspector" data-status="empty" className={className} style={panelStyle}>
        <p style={{ padding: 12, color: "#9ca3af", fontSize: 12, margin: 0 }}>No project loaded.</p>
      </div>
    );
  }

  const widthDirty = Math.abs(width - persistedWidth) > 1e-9;

  return (
    <div
      data-testid="net-inspector"
      data-status="ready"
      data-mode={resolved.mode}
      data-source={resolved.source}
      data-dirty={widthDirty ? "true" : "false"}
      className={className}
      style={panelStyle}
    >
      <header style={headerStyle}>
        <span style={{ flex: 1 }}>Net inspector</span>
        <select
          value={selectedNet}
          onChange={(e) => setSelectedNet(e.target.value)}
          data-testid="net-inspector-select"
          style={selectStyle}
        >
          {nets.map((n) => (
            <option key={n.name} value={n.name}>
              {n.name || "(unnamed)"}
            </option>
          ))}
        </select>
      </header>

      {wasmError ? (
        <div data-testid="net-inspector-wasm-error" style={errorRowStyle}>
          solver: {wasmError}
        </div>
      ) : null}
      {error ? (
        <div data-testid="net-inspector-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}

      <div style={bodyStyle}>
        <div style={infoRowStyle}>
          <span data-testid="net-inspector-layer">Layer: {homeLayer}</span>
          <span data-testid="net-inspector-mode">Mode: {resolved.mode}</span>
          <span data-testid="net-inspector-class">Class: {className_}</span>
          {resolved.source === "fallback" ? (
            <span data-testid="net-inspector-fallback" style={{ color: "#fbbf24" }}>
              (no stackup — using FR-4 default)
            </span>
          ) : null}
        </div>
        <div style={infoRowStyle}>
          <span>H = {resolved.height_mm.toFixed(3)} mm</span>
          <span>εr = {resolved.dielectric_constant.toFixed(2)}</span>
          <span>T = {resolved.copper_thickness_mm.toFixed(3)} mm</span>
        </div>

        <label style={sliderRowStyle}>
          <span style={{ width: 70 }}>Width</span>
          <input
            type="range"
            min={0.05}
            max={2.0}
            step={0.005}
            value={width}
            onChange={(e) => setWidth(Number.parseFloat(e.target.value))}
            data-testid="net-inspector-width-slider"
            style={{ flex: 1 }}
          />
          <input
            type="number"
            min={0.05}
            step={0.005}
            value={width}
            onChange={(e) => setWidth(Number.parseFloat(e.target.value) || 0)}
            data-testid="net-inspector-width-number"
            style={inputStyle(80)}
          />
          <span style={{ color: "#9ca3af", fontSize: 11 }}>mm</span>
        </label>

        <div style={readoutRowStyle}>
          <div style={readoutCellStyle}>
            <span style={readoutLabelStyle}>
              {resolved.mode === "stripline" ? "Stripline Z₀ (IPC)" : "Microstrip Z₀ (Hammerstad)"}
            </span>
            <span data-testid="net-inspector-z0-primary" style={readoutValueStyle}>
              {primaryZ0 == null
                ? wasm
                  ? "—"
                  : "loading…"
                : `${primaryZ0.toFixed(1)} Ω`}
            </span>
          </div>
          <div style={readoutCellStyle}>
            <span style={readoutLabelStyle}>Microstrip Z₀ (IPC-2141)</span>
            <span data-testid="net-inspector-z0-ipc" style={readoutValueStyle}>
              {z0 == null ? (wasm ? "—" : "loading…") : `${z0.ipc2141.toFixed(1)} Ω`}
            </span>
          </div>
        </div>

        <div style={snapRowStyle}>
          <span style={{ color: "#9ca3af", fontSize: 11, marginRight: 4 }}>Snap to:</span>
          {SNAP_TARGETS_OHMS.map((target) => (
            <button
              key={target}
              type="button"
              onClick={() => snap(target)}
              disabled={!wasm}
              style={snapButtonStyle}
              data-testid={`net-inspector-snap-${target}`}
            >
              {target} Ω
            </button>
          ))}
        </div>

        <div style={applyRowStyle}>
          <span data-testid="net-inspector-persisted" style={{ color: "#9ca3af", fontSize: 11 }}>
            Persisted: {persistedWidth.toFixed(3)} mm
          </span>
          <button
            type="button"
            onClick={() => setWidth(persistedWidth)}
            disabled={!widthDirty || busy}
            style={revertButtonStyle(widthDirty && !busy)}
            data-testid="net-inspector-revert"
          >
            Revert
          </button>
          <button
            type="button"
            onClick={() => void apply()}
            disabled={!widthDirty || busy}
            style={applyButtonStyle(widthDirty && !busy)}
            data-testid="net-inspector-apply"
          >
            {busy ? "Applying…" : `Apply to ${className_}`}
          </button>
        </div>
      </div>
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  background: "#10131a",
  border: "1px solid #1f2330",
  borderRadius: 6,
  overflow: "auto",
  fontSize: 12,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 12px",
  borderBottom: "1px solid #1f2330",
  fontWeight: 600,
  color: "#cbd5e1",
  letterSpacing: 0.4,
  textTransform: "uppercase",
  background: "#161b25",
};

const bodyStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 12,
  color: "#e2e8f0",
};

const infoRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 16,
  fontSize: 11,
  color: "#cbd5e1",
};

const sliderRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: 12,
};

const readoutRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 12,
};

const readoutCellStyle: React.CSSProperties = {
  flex: 1,
  display: "flex",
  flexDirection: "column",
  gap: 2,
  background: "#0d1018",
  border: "1px solid #1f2330",
  borderRadius: 4,
  padding: "8px 10px",
};

const readoutLabelStyle: React.CSSProperties = {
  fontSize: 10,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  color: "#9ca3af",
};

const readoutValueStyle: React.CSSProperties = {
  fontFamily: "monospace",
  fontSize: 18,
  color: "#f9fafb",
};

const snapRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const snapButtonStyle: React.CSSProperties = {
  padding: "3px 10px",
  background: "#1f2937",
  color: "#e2e8f0",
  border: "1px solid #2a3140",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
};

const applyRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  borderTop: "1px solid #1f2330",
  paddingTop: 8,
};

function applyButtonStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 12px",
    background: active ? "#1e40af" : "#1f2937",
    color: active ? "#f9fafb" : "#9ca3af",
    border: "none",
    borderRadius: 3,
    cursor: active ? "pointer" : "default",
    fontSize: 11,
    marginLeft: "auto",
  };
}

function revertButtonStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 10px",
    background: "transparent",
    color: active ? "#cbd5e1" : "#52525b",
    border: "1px solid " + (active ? "#3f3f46" : "#27272a"),
    borderRadius: 3,
    cursor: active ? "pointer" : "default",
    fontSize: 11,
  };
}

const selectStyle: React.CSSProperties = {
  padding: "4px 6px",
  background: "#0d1018",
  color: "#f9fafb",
  border: "1px solid #1f2330",
  borderRadius: 3,
  fontSize: 12,
};

function inputStyle(width: number): React.CSSProperties {
  return {
    width,
    padding: "4px 6px",
    background: "#0d1018",
    color: "#f9fafb",
    border: "1px solid #1f2330",
    borderRadius: 3,
    fontSize: 12,
  };
}

const errorRowStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "rgba(255, 77, 79, 0.15)",
  color: "#ff7875",
  fontSize: 11,
  borderBottom: "1px solid #401b1b",
};
