import { useCallback, useEffect, useState } from "react";

export type FabTarget = "jlcpcb" | "oshpark" | "pcbway" | "generic";

export interface DfmIssue {
  severity: "error" | "warning";
  rule: string;
  description: string;
  items: string[];
  actual_mm: number;
  limit_mm: number;
}

export interface DfmCheckResult {
  ok: boolean;
  target: FabTarget;
  issues: DfmIssue[];
  counts: { error: number; warning: number };
}

export interface FabExportArtifact {
  ok: boolean;
  files?: string[];
  output_dir?: string;
  skipped?: boolean;
  reason?: string;
  error?: string;
}

export interface FabExportResult {
  ok: boolean;
  target: FabTarget;
  pcb_path: string;
  output_dir: string;
  artifacts: {
    gerbers: FabExportArtifact;
    drill: FabExportArtifact;
    pos: FabExportArtifact;
    bom: FabExportArtifact;
  };
}

export interface FabExportDialogApi {
  /** Currently chosen fab. Defaults to `generic` until the user
   *  picks. */
  target: FabTarget;
  /** Set the active target. Clears any stale DFM result. */
  setTarget: (target: FabTarget) => void;
  /** Output directory for the exported bundle. */
  outputDir: string;
  setOutputDir: (path: string) => void;
  /** Most recent DFM dry-run. */
  dfm: DfmCheckResult | null;
  /** Most recent export result (post-Export click). */
  exportResult: FabExportResult | null;
  /** True while the corresponding async call is in flight. */
  dfmRunning: boolean;
  exporting: boolean;
  /** Last surfaced error from either call. */
  error: string | null;
  /** Trigger the DFM dry-run. */
  runDfm: () => Promise<DfmCheckResult>;
  /** Trigger the fab export. Refuses when the most recent DFM
   *  result has unresolved errors. */
  exportBundle: () => Promise<FabExportResult | null>;
  /** Clear results + error. */
  reset: () => void;
}

export interface FabExportDialogProps {
  projectId: string;
  /** Path to the `.kicad_pcb` file. The fab export endpoint
   *  shells `kicad-cli` against it. */
  pcbPath: string;
  /** Optional path to the `.kicad_sch` so the export includes a
   *  BOM. Empty/undefined → BOM is skipped server-side. */
  schPath?: string;
  /** Default output directory for the bundle. */
  defaultOutputDir?: string;
  /** Gateway base — defaults to `/api`. */
  apiBase?: string;
  /** Test seam — defaults to `fetch`. */
  fetcher?: typeof fetch;
  /** Optional notify-parent on export completion. */
  onExported?: (result: FabExportResult) => void;
}

const SUPPORTED_TARGETS: FabTarget[] = [
  "jlcpcb",
  "oshpark",
  "pcbway",
  "generic",
];

/**
 * `useFabExportDialog` (M2-T-09) — drives the fab-bundle export
 * flow.
 *
 * Flow:
 *   1. User picks a target (JLCPCB / OSHPark / PCBWay / generic).
 *   2. `Run DFM` GETs `/api/server/project/<id>/dfm/check?target=…`
 *      — the M2-Q-03 dry-run that flags fab-spec violations.
 *   3. The dialog renders the issue list grouped by severity.
 *   4. `Export` is disabled while `dfm == null` or
 *      `dfm.counts.error > 0`. Warnings are non-blocking
 *      (per the M2-Q-03 contract).
 *   5. On Export, POSTs to the four `kiconnector` endpoints
 *      (`/api/connector/tools/{gerbers,drill,pos,bom}`) in
 *      parallel, mirroring what `kc_export_fab` does Claude-side.
 */
export function useFabExportDialog(
  props: FabExportDialogProps,
): FabExportDialogApi {
  const {
    projectId,
    pcbPath,
    schPath,
    defaultOutputDir = "fab",
    apiBase = "/api",
    fetcher,
    onExported,
  } = props;

  const [target, setTargetState] = useState<FabTarget>("generic");
  const [outputDir, setOutputDir] = useState<string>(defaultOutputDir);
  const [dfm, setDfm] = useState<DfmCheckResult | null>(null);
  const [exportResult, setExportResult] = useState<FabExportResult | null>(null);
  const [dfmRunning, setDfmRunning] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const setTarget = useCallback((next: FabTarget) => {
    setTargetState(next);
    // A DFM result is target-specific — drop it when the target
    // changes so the dialog can't ship a stale gate decision.
    setDfm(null);
    setExportResult(null);
    setError(null);
  }, []);

  const runDfm = useCallback(async (): Promise<DfmCheckResult> => {
    setDfmRunning(true);
    setError(null);
    try {
      const url = `${apiBase}/server/project/${encodeURIComponent(projectId)}/dfm/check?target=${target}`;
      const resp = await fetchImpl(url);
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      const body = (await resp.json()) as DfmCheckResult & {
        detail?: string;
      };
      setDfm(body);
      return body;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      const fallback: DfmCheckResult = {
        ok: false,
        target,
        issues: [],
        counts: { error: 0, warning: 0 },
      };
      setDfm(fallback);
      return fallback;
    } finally {
      setDfmRunning(false);
    }
  }, [apiBase, fetchImpl, projectId, target]);

  const exportBundle =
    useCallback(async (): Promise<FabExportResult | null> => {
      if (!dfm) {
        setError("Run the DFM check before exporting.");
        return null;
      }
      if (dfm.counts.error > 0) {
        setError(
          `${dfm.counts.error} DFM errors remain — fix them before exporting.`,
        );
        return null;
      }
      setExporting(true);
      setError(null);
      const connector = `${apiBase}/connector`;
      const baseBody = { pcb_path: pcbPath, output_dir: outputDir };
      const posSide =
        target === "generic" || target === "jlcpcb" ? "both" : "front";
      try {
        const [gerbers, drill, pos, bom] = await Promise.all([
          postArtifact(
            fetchImpl,
            `${connector}/tools/gerbers`,
            baseBody,
          ),
          postArtifact(fetchImpl, `${connector}/tools/drill`, baseBody),
          postArtifact(fetchImpl, `${connector}/tools/pos`, {
            ...baseBody,
            side: posSide,
          }),
          schPath
            ? postArtifact(fetchImpl, `${connector}/tools/bom`, {
                sch_path: schPath,
                output_dir: outputDir,
              })
            : Promise.resolve<FabExportArtifact>({
                ok: true,
                skipped: true,
                reason: "no schematic supplied",
              }),
        ]);
        const result: FabExportResult = {
          ok:
            gerbers.ok && drill.ok && pos.ok && (bom.ok || bom.skipped === true),
          target,
          pcb_path: pcbPath,
          output_dir: outputDir,
          artifacts: { gerbers, drill, pos, bom },
        };
        setExportResult(result);
        onExported?.(result);
        return result;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return null;
      } finally {
        setExporting(false);
      }
    }, [apiBase, dfm, fetchImpl, onExported, outputDir, pcbPath, schPath, target]);

  const reset = useCallback(() => {
    setDfm(null);
    setExportResult(null);
    setError(null);
  }, []);

  return {
    target,
    setTarget,
    outputDir,
    setOutputDir,
    dfm,
    exportResult,
    dfmRunning,
    exporting,
    error,
    runDfm,
    exportBundle,
    reset,
  };
}

async function postArtifact(
  fetchImpl: typeof fetch,
  url: string,
  body: Record<string, unknown>,
): Promise<FabExportArtifact> {
  try {
    const resp = await fetchImpl(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = (await resp.json()) as FabExportArtifact;
    if (!resp.ok) {
      return {
        ok: false,
        error: result?.error ?? `${resp.status} ${resp.statusText}`,
      };
    }
    return result;
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export interface FabExportDialogComponentProps extends FabExportDialogProps {
  /** Optional className for the modal wrapper. */
  className?: string;
}

/**
 * Modal-style dialog: target picker, output-dir input, Run DFM
 * button, DFM issue list, Export button, post-export artifact
 * summary.
 */
export function FabExportDialog(props: FabExportDialogComponentProps) {
  const { className, ...api } = props;
  const dialog = useFabExportDialog(api);
  const blocked =
    dialog.dfm === null || dialog.dfm.counts.error > 0 || dialog.exporting;

  // Render-time guard: when target changes we want the issue list
  // to reflect the active target name, so re-derive the display
  // name on each render.
  useEffect(() => {
    // No side-effect — placeholder for future analytics hook.
  }, [dialog.target]);

  return (
    <section
      data-testid="fab-export-dialog"
      className={className}
      style={dialogStyle}
    >
      <header style={headerStyle}>Fab export</header>
      <div style={bodyStyle}>
        <label style={labelStyle}>
          Target
          <select
            data-testid="fab-target-select"
            value={dialog.target}
            onChange={(e) => dialog.setTarget(e.target.value as FabTarget)}
            style={selectStyle}
          >
            {SUPPORTED_TARGETS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label style={labelStyle}>
          Output directory
          <input
            data-testid="fab-output-dir"
            type="text"
            value={dialog.outputDir}
            onChange={(e) => dialog.setOutputDir(e.target.value)}
            style={inputStyle}
          />
        </label>
        <div style={buttonRowStyle}>
          <button
            type="button"
            onClick={() => void dialog.runDfm()}
            disabled={dialog.dfmRunning}
            data-testid="fab-dfm-run"
            style={secondaryButtonStyle(dialog.dfmRunning)}
          >
            {dialog.dfmRunning ? "Running DFM…" : "Run DFM"}
          </button>
          <button
            type="button"
            onClick={() => {
              void dialog.exportBundle();
            }}
            disabled={blocked}
            data-testid="fab-export"
            style={primaryButtonStyle(blocked)}
          >
            {dialog.exporting ? "Exporting…" : "Export"}
          </button>
        </div>
        {dialog.error ? (
          <p data-testid="fab-error" style={errorMessageStyle}>
            {dialog.error}
          </p>
        ) : null}
        {dialog.dfm ? (
          <div data-testid="fab-dfm-results" style={resultsStyle}>
            <div style={countsStyle}>
              DFM — {dialog.dfm.counts.error} errors,{" "}
              {dialog.dfm.counts.warning} warnings
            </div>
            <ul style={dfmListStyle}>
              {dialog.dfm.issues.map((iss, i) => (
                <li
                  key={`iss-${i}`}
                  data-testid="fab-dfm-issue"
                  data-severity={iss.severity}
                  style={dfmRowStyle(iss.severity)}
                >
                  <strong>{iss.rule}</strong>{" "}
                  <span style={{ color: "#9ca3af" }}>
                    {iss.items.join(", ")}
                  </span>
                  <div style={{ fontSize: 11 }}>{iss.description}</div>
                </li>
              ))}
              {dialog.dfm.issues.length === 0 ? (
                <li style={dfmEmptyStyle}>No DFM issues — ready to export.</li>
              ) : null}
            </ul>
          </div>
        ) : null}
        {dialog.exportResult ? (
          <div data-testid="fab-export-summary" style={resultsStyle}>
            <div style={countsStyle}>
              Bundle written to {dialog.exportResult.output_dir}
            </div>
            <ul style={dfmListStyle}>
              {Object.entries(dialog.exportResult.artifacts).map(([k, v]) => (
                <li
                  key={`art-${k}`}
                  data-testid="fab-artifact-row"
                  data-artifact={k}
                  data-ok={v.ok ? "true" : "false"}
                  style={dfmRowStyle(v.ok ? "warning" : "error")}
                >
                  <strong>{k}</strong>:{" "}
                  {v.skipped
                    ? `skipped (${v.reason ?? "no reason"})`
                    : v.ok
                      ? `${v.files?.length ?? 0} files`
                      : `error — ${v.error ?? "unknown"}`}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

const dialogStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  width: 480,
  maxHeight: "80vh",
  background: "#10131a",
  border: "1px solid #1f2330",
  borderRadius: 8,
  overflow: "hidden",
  color: "#e2e8f0",
  fontSize: 12,
};

const headerStyle: React.CSSProperties = {
  padding: "8px 14px",
  borderBottom: "1px solid #1f2330",
  background: "#161b25",
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  color: "#cbd5e1",
};

const bodyStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 14,
  overflowY: "auto",
};

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  fontSize: 11,
  color: "#cbd5e1",
};

const selectStyle: React.CSSProperties = {
  padding: "6px 8px",
  background: "#0d1018",
  color: "#f9fafb",
  border: "1px solid #1f2330",
  borderRadius: 4,
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  ...selectStyle,
  width: "100%",
};

const buttonRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
};

function secondaryButtonStyle(busy: boolean): React.CSSProperties {
  return {
    padding: "6px 10px",
    background: "transparent",
    color: busy ? "#9ca3af" : "#e2e8f0",
    border: "1px solid #1f2937",
    borderRadius: 4,
    cursor: busy ? "wait" : "pointer",
    fontSize: 12,
  };
}

function primaryButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    padding: "6px 12px",
    background: disabled ? "#1f2937" : "#1e40af",
    color: disabled ? "#9ca3af" : "#f9fafb",
    border: "none",
    borderRadius: 4,
    cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 12,
    fontWeight: 600,
  };
}

const errorMessageStyle: React.CSSProperties = {
  padding: "6px 8px",
  background: "rgba(255,77,79,0.15)",
  color: "#ff7875",
  borderRadius: 4,
  fontSize: 11,
  margin: 0,
};

const resultsStyle: React.CSSProperties = {
  borderTop: "1px solid #1a1f2a",
  paddingTop: 8,
};

const countsStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#9ca3af",
  marginBottom: 4,
};

const dfmListStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

function dfmRowStyle(severity: "error" | "warning"): React.CSSProperties {
  return {
    padding: "6px 8px",
    borderLeft: `3px solid ${severity === "error" ? "#ff4d4f" : "#f0a500"}`,
    background: "#0d1018",
    borderRadius: 3,
    fontSize: 12,
  };
}

const dfmEmptyStyle: React.CSSProperties = {
  padding: "6px 8px",
  color: "#9ca3af",
  fontSize: 11,
};
