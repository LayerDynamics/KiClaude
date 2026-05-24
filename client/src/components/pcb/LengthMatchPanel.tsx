/**
 * `LengthMatchPanel` (M3-T-04) — group manager + live analyzer view.
 *
 * Two stacked sections:
 *
 *  1. Group table — one row per declared `pcb.length_group`. Editable
 *     cells: name, members (CSV), target length (mm), tolerance (mm).
 *     Per-row Save + delete go through `ui_lengthgroup_set` /
 *     `_delete` on the gateway.
 *  2. Analyzer report — for each group, the rows from the M3-R-05
 *     analyzer (`crates/cad/src/length_match.rs::analyze`) called via
 *     the `analyzeLengthMatch` wasm shim. Renders each member's
 *     current routed length, delta vs target, and serpentine
 *     suggestion when `TooShort`.
 *
 * The analyzer fires whenever `pcb.length_groups` or `pcb.tracks`
 * changes (the inputs it actually consumes), keyed on a stable
 * project snapshot. Empty groups are surfaced with a "no members"
 * note rather than hidden, so the user doesn't have to debug missing
 * report rows by inference.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { loadKiclaudeWasm } from "../../lib/wasm";
import { useProjectStore } from "../../stores/projectStore";
import type { KcirLengthGroup, KcirProject } from "../../stores/projectStore";

interface AnalyzerWasm {
  analyzeLengthMatch(pcb_json: string): string;
}

export type LengthMatchStatus =
  | "in_range"
  | "too_short"
  | "too_long"
  | "unrouted";

export interface LengthMatchMember {
  net: string;
  current_length_mm: number;
  delta_mm: number;
  status: LengthMatchStatus;
  suggested_serpentine_count: number;
  suggested_segment_gain_mm: number;
}

export interface LengthMatchReport {
  name: string;
  target_length_mm: number;
  tolerance_mm: number;
  members: LengthMatchMember[];
}

export interface LengthMatchPanelProps {
  projectId: string;
  apiBase?: string;
  fetcher?: typeof fetch;
  wasmLoader?: () => Promise<{ cad: AnalyzerWasm }>;
  className?: string;
  onUpserted?: (group: KcirLengthGroup) => void;
  onDeleted?: (name: string) => void;
}

interface DraftGroup extends KcirLengthGroup {
  /** CSV of net names — kept as a string so the user can type
   * `DQ0, DQ1, DQ2` without the panel constantly re-splitting on
   * each keystroke. Committed to `nets` on Save. */
  _nets_csv: string;
}

const NEW_BASE: DraftGroup = {
  name: "",
  nets: [],
  target_length_mm: 0,
  tolerance_mm: 0.127,
  _nets_csv: "",
};

function csv(nets: string[]): string {
  return nets.join(", ");
}

function splitCsv(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function statusLabel(s: LengthMatchStatus): string {
  switch (s) {
    case "in_range":
      return "✓ matched";
    case "too_short":
      return "− too short";
    case "too_long":
      return "+ too long";
    case "unrouted":
      return "○ unrouted";
  }
}

function statusColor(s: LengthMatchStatus): string {
  switch (s) {
    case "in_range":
      return "#34d399";
    case "too_short":
      return "#fbbf24";
    case "too_long":
      return "#ff7875";
    case "unrouted":
      return "#9ca3af";
  }
}

export function LengthMatchPanel(props: LengthMatchPanelProps) {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    wasmLoader = loadKiclaudeWasm as () => Promise<{ cad: AnalyzerWasm }>,
    className,
    onUpserted,
    onDeleted,
  } = props;

  const project = useProjectStore((s) => s.project);
  const persistedGroups = useMemo<KcirLengthGroup[]>(
    () => project?.pcb.length_groups ?? [],
    [project],
  );
  const availableNets = useMemo<string[]>(
    () => (project?.pcb.nets ?? []).map((n) => n.name).filter((n) => n.length > 0),
    [project],
  );
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const initialRows = useMemo<DraftGroup[]>(
    () =>
      persistedGroups.map((g) => ({
        ...g,
        _nets_csv: csv(g.nets),
      })),
    [persistedGroups],
  );
  const [rows, setRows] = useState<DraftGroup[]>(initialRows);
  const [draft, setDraft] = useState<DraftGroup>({ ...NEW_BASE });
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(initialRows);
    setDirtyKeys(new Set());
    setError(null);
  }, [initialRows]);

  // --- analyzer -----------------------------------------------------

  const [wasm, setWasm] = useState<AnalyzerWasm | null>(null);
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

  const reports: LengthMatchReport[] = useMemo(() => {
    if (!wasm || !project) return [];
    try {
      const raw = wasm.analyzeLengthMatch(JSON.stringify(buildAnalyzerPcb(project)));
      return JSON.parse(raw) as LengthMatchReport[];
    } catch (err) {
      setWasmError(err instanceof Error ? err.message : String(err));
      return [];
    }
  }, [wasm, project]);

  const reportByName = useMemo(() => {
    const map = new Map<string, LengthMatchReport>();
    for (const r of reports) map.set(r.name, r);
    return map;
  }, [reports]);

  // --- mutations ----------------------------------------------------

  const markDirty = useCallback((key: string) => {
    setDirtyKeys((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  }, []);

  const updateRow = useCallback(
    (index: number, patch: Partial<DraftGroup>) => {
      setRows((prev) => {
        const next = prev.slice();
        const current = next[index];
        if (!current) return prev;
        next[index] = { ...current, ...patch };
        return next;
      });
      const key = rows[index]?.name ?? `<row-${index}>`;
      markDirty(key);
    },
    [markDirty, rows],
  );

  const post = useCallback(
    async (
      tool: "ui_lengthgroup_set" | "ui_lengthgroup_delete",
      args: Record<string, unknown>,
    ) => {
      const url = `${apiBase}/${tool}/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args }),
      });
      const body = (await resp.json()) as {
        ok?: boolean;
        length_group?: KcirLengthGroup;
        deleted?: string;
        error?: string;
        detail?: string;
      };
      if (!resp.ok || body.ok !== true) {
        const err = body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`;
        throw new Error(err);
      }
      return body;
    },
    [apiBase, fetchImpl, projectId],
  );

  const saveRow = useCallback(async (index: number) => {
    const row = rows[index];
    if (!row) return;
    const key = row.name || `<row-${index}>`;
    setBusyKey(key);
    setError(null);
    try {
      const body = await post("ui_lengthgroup_set", {
        name: row.name,
        nets: splitCsv(row._nets_csv),
        target_length_mm: row.target_length_mm,
        tolerance_mm: row.tolerance_mm,
      });
      if (body.length_group) onUpserted?.(body.length_group);
      setDirtyKeys((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }, [rows, post, onUpserted]);

  const declareNew = useCallback(async () => {
    if (!draft.name.trim()) {
      setError("`name` is required");
      return;
    }
    const nets = splitCsv(draft._nets_csv);
    if (nets.length === 0) {
      setError("at least one member net is required");
      return;
    }
    setBusyKey(draft.name);
    setError(null);
    try {
      const body = await post("ui_lengthgroup_set", {
        name: draft.name,
        nets,
        target_length_mm: draft.target_length_mm,
        tolerance_mm: draft.tolerance_mm,
      });
      if (body.length_group) {
        onUpserted?.(body.length_group);
        setRows((prev) => [
          ...prev,
          { ...body.length_group!, _nets_csv: csv(body.length_group!.nets) },
        ]);
      }
      setDraft({ ...NEW_BASE });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }, [draft, post, onUpserted]);

  const deleteRow = useCallback(async (index: number) => {
    const row = rows[index];
    if (!row) return;
    const key = row.name;
    if (!key) {
      setRows((prev) => prev.filter((_, i) => i !== index));
      return;
    }
    setBusyKey(key);
    setError(null);
    try {
      await post("ui_lengthgroup_delete", { name: key });
      setRows((prev) => prev.filter((_, i) => i !== index));
      onDeleted?.(key);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }, [rows, post, onDeleted]);

  if (!project) {
    return (
      <div
        data-testid="length-match-panel"
        data-status="empty"
        className={className}
        style={panelStyle}
      >
        <p style={{ padding: 12, color: "#9ca3af", fontSize: 12, margin: 0 }}>
          No project loaded.
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="length-match-panel"
      data-status="ready"
      className={className}
      style={panelStyle}
    >
      <header style={headerStyle}>
        <span style={{ flex: 1 }}>Length-match groups</span>
        <span data-testid="length-match-count" style={{ color: "#9ca3af", fontSize: 11 }}>
          {rows.length} declared · {reports.length} analyzed
        </span>
      </header>

      {wasmError ? (
        <div data-testid="length-match-wasm-error" style={errorRowStyle}>
          analyzer: {wasmError}
        </div>
      ) : null}
      {error ? (
        <div data-testid="length-match-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}

      <table style={tableStyle}>
        <thead>
          <tr style={{ color: "#9ca3af" }}>
            <th style={thStyle}>Name</th>
            <th style={thStyle}>Members (CSV)</th>
            <th style={thStyle}>Target mm</th>
            <th style={thStyle}>Tol mm</th>
            <th style={thStyle}>Status</th>
            <th style={thStyle}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const key = row.name || `<row-${i}>`;
            const dirty = dirtyKeys.has(key);
            const busy = busyKey === key;
            const report = reportByName.get(row.name);
            return (
              <tr
                key={`group-${i}`}
                data-testid="length-match-row"
                data-group-name={row.name}
                data-dirty={dirty ? "true" : "false"}
              >
                <td style={tdStyle}>
                  <input
                    type="text"
                    value={row.name}
                    onChange={(e) => updateRow(i, { name: e.target.value })}
                    style={inputStyle(140)}
                    data-testid="length-match-name"
                  />
                </td>
                <td style={tdStyle}>
                  <input
                    type="text"
                    value={row._nets_csv}
                    onChange={(e) => updateRow(i, { _nets_csv: e.target.value })}
                    style={inputStyle(260)}
                    placeholder="DQ0, DQ1, DQ2"
                    data-testid="length-match-nets"
                    list={`length-nets-${i}`}
                  />
                  <datalist id={`length-nets-${i}`}>
                    {availableNets.map((n) => (
                      <option key={n} value={n} />
                    ))}
                  </datalist>
                </td>
                <td style={tdStyle}>
                  <input
                    type="number"
                    step="0.05"
                    min="0"
                    value={row.target_length_mm}
                    onChange={(e) =>
                      updateRow(i, {
                        target_length_mm: Number.parseFloat(e.target.value) || 0,
                      })
                    }
                    style={inputStyle(80)}
                    data-testid="length-match-target"
                  />
                </td>
                <td style={tdStyle}>
                  <input
                    type="number"
                    step="0.005"
                    min="0"
                    value={row.tolerance_mm}
                    onChange={(e) =>
                      updateRow(i, {
                        tolerance_mm: Number.parseFloat(e.target.value) || 0,
                      })
                    }
                    style={inputStyle(70)}
                    data-testid="length-match-tolerance"
                  />
                </td>
                <td style={tdStyle}>
                  <GroupStatusCell report={report} />
                </td>
                <td style={tdStyle}>
                  <button
                    type="button"
                    onClick={() => void saveRow(i)}
                    disabled={!dirty || busy || !row.name}
                    style={saveButtonStyle(dirty && !busy)}
                    data-testid="length-match-save"
                  >
                    {busy ? "…" : "Save"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void deleteRow(i)}
                    disabled={busy}
                    style={deleteButtonStyle}
                    data-testid="length-match-delete"
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <ReportDetail reports={reports} />

      <div style={newRowSection}>
        <div style={{ ...headerStyle, borderTop: "1px solid #1f2330", background: "#0e1119" }}>
          <span style={{ flex: 1 }}>Declare new group</span>
        </div>
        <div style={newRowGrid}>
          <label style={fieldLabel}>
            Name
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              style={inputStyle(160)}
              placeholder="DDR3_DQ_BYTE0"
              data-testid="length-match-new-name"
            />
          </label>
          <label style={{ ...fieldLabel, flex: 2, minWidth: 280 }}>
            Members (CSV)
            <input
              type="text"
              value={draft._nets_csv}
              onChange={(e) => setDraft((d) => ({ ...d, _nets_csv: e.target.value }))}
              style={inputStyle(260)}
              placeholder="DQ0, DQ1, DQ2, DQ3"
              data-testid="length-match-new-nets"
            />
          </label>
          <label style={fieldLabel}>
            Target mm (0 = match longest)
            <input
              type="number"
              step="0.05"
              min="0"
              value={draft.target_length_mm}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  target_length_mm: Number.parseFloat(e.target.value) || 0,
                }))
              }
              style={inputStyle(100)}
              data-testid="length-match-new-target"
            />
          </label>
          <label style={fieldLabel}>
            Tolerance mm
            <input
              type="number"
              step="0.005"
              min="0"
              value={draft.tolerance_mm}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  tolerance_mm: Number.parseFloat(e.target.value) || 0,
                }))
              }
              style={inputStyle(80)}
              data-testid="length-match-new-tolerance"
            />
          </label>
          <button
            type="button"
            onClick={() => void declareNew()}
            disabled={busyKey === draft.name}
            style={addButtonStyle}
            data-testid="length-match-declare"
          >
            Declare
          </button>
        </div>
      </div>
    </div>
  );
}

function GroupStatusCell({ report }: { report: LengthMatchReport | undefined }) {
  if (!report) {
    return (
      <span data-testid="length-match-status-none" style={{ color: "#9ca3af", fontSize: 11 }}>
        (no report)
      </span>
    );
  }
  if (report.members.length === 0) {
    return (
      <span data-testid="length-match-status-empty" style={{ color: "#fbbf24", fontSize: 11 }}>
        no members
      </span>
    );
  }
  const counts = report.members.reduce<Record<LengthMatchStatus, number>>(
    (acc, m) => {
      acc[m.status] += 1;
      return acc;
    },
    { in_range: 0, too_short: 0, too_long: 0, unrouted: 0 },
  );
  return (
    <span style={{ display: "flex", gap: 6, fontSize: 11 }}>
      <span style={{ color: statusColor("in_range") }} data-testid="length-match-status-inrange">
        ✓{counts.in_range}
      </span>
      <span style={{ color: statusColor("too_short") }} data-testid="length-match-status-short">
        −{counts.too_short}
      </span>
      <span style={{ color: statusColor("too_long") }} data-testid="length-match-status-long">
        +{counts.too_long}
      </span>
      <span style={{ color: statusColor("unrouted") }} data-testid="length-match-status-unrouted">
        ○{counts.unrouted}
      </span>
    </span>
  );
}

function ReportDetail({ reports }: { reports: LengthMatchReport[] }) {
  if (reports.length === 0) return null;
  return (
    <div style={detailWrap}>
      {reports.map((r) => (
        <div key={r.name} data-testid="length-match-report" data-report-name={r.name} style={reportBlock}>
          <div style={reportHeader}>
            <strong>{r.name}</strong>
            <span style={{ color: "#9ca3af", fontSize: 11 }}>
              target {r.target_length_mm.toFixed(3)} mm · tol ±{r.tolerance_mm.toFixed(3)} mm
            </span>
          </div>
          {r.members.length === 0 ? (
            <p style={{ color: "#fbbf24", fontSize: 11, margin: 0 }}>no members</p>
          ) : (
            <table style={{ ...tableStyle, marginTop: 4 }}>
              <thead>
                <tr style={{ color: "#9ca3af" }}>
                  <th style={thStyle}>Net</th>
                  <th style={thStyle}>Length mm</th>
                  <th style={thStyle}>Δ mm</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Suggestion</th>
                </tr>
              </thead>
              <tbody>
                {r.members.map((m) => (
                  <tr key={m.net} data-testid="length-match-report-member" data-net-name={m.net}>
                    <td style={tdStyle}>{m.net}</td>
                    <td style={tdStyle}>{m.current_length_mm.toFixed(3)}</td>
                    <td style={{ ...tdStyle, color: statusColor(m.status) }}>
                      {m.delta_mm >= 0 ? "+" : ""}
                      {m.delta_mm.toFixed(3)}
                    </td>
                    <td style={{ ...tdStyle, color: statusColor(m.status) }}>
                      {statusLabel(m.status)}
                    </td>
                    <td style={tdStyle}>
                      {m.suggested_serpentine_count > 0
                        ? `${m.suggested_serpentine_count} serpentines × ${m.suggested_segment_gain_mm.toFixed(3)} mm`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  );
}

/** Pull just the fields the analyzer reads — full project objects can
 * carry tens-of-KB of footprints/zones that don't affect length and
 * would just slow the JSON round-trip on every render. */
function buildAnalyzerPcb(project: KcirProject): Record<string, unknown> {
  return {
    version: 0,
    generator: "kiclaude",
    thickness_mm: 0,
    paper: "",
    pad_to_mask_clearance_mm: 0,
    solder_mask_min_width_mm: 0,
    net_classes: [],
    layers: [],
    footprints: [],
    tracks: project.pcb.tracks ?? [],
    vias: [],
    zones: [],
    outline: { points_mm: [], cutouts: [] },
    drawings: [],
    nets: project.pcb.nets ?? [],
    diff_pairs: [],
    length_groups: project.pcb.length_groups ?? [],
  };
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

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 12,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  borderBottom: "1px solid #1f2330",
};

const tdStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid #1a1f2a",
  color: "#e2e8f0",
};

const newRowSection: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const newRowGrid: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "flex-end",
  gap: 12,
  padding: 12,
};

const fieldLabel: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  color: "#cbd5e1",
  fontSize: 11,
};

const errorRowStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "rgba(255, 77, 79, 0.15)",
  color: "#ff7875",
  fontSize: 11,
  borderBottom: "1px solid #401b1b",
};

const detailWrap: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "8px 12px",
  borderTop: "1px solid #1f2330",
};

const reportBlock: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  background: "#0d1018",
  border: "1px solid #1a1f2a",
  borderRadius: 4,
  padding: 8,
};

const reportHeader: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  color: "#e2e8f0",
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

function saveButtonStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 8px",
    background: active ? "#1e40af" : "#1f2937",
    color: active ? "#f9fafb" : "#9ca3af",
    border: "none",
    borderRadius: 3,
    cursor: active ? "pointer" : "default",
    fontSize: 11,
    marginRight: 4,
  };
}

const deleteButtonStyle: React.CSSProperties = {
  padding: "2px 6px",
  background: "transparent",
  color: "#ff7875",
  border: "1px solid #401b1b",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 12,
};

const addButtonStyle: React.CSSProperties = {
  padding: "6px 14px",
  background: "#1e40af",
  color: "#f9fafb",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
};
