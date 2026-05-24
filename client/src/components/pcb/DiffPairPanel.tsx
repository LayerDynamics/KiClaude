/**
 * `DiffPairPanel` (M3-T-03) — declare / inspect / remove differential
 * pairs.
 *
 * Rows: `name · positive net · negative net · Zdiff target · gap · length
 * group · skew tol · ×`. Edits in a row mark it dirty; Save POSTs the
 * row's values to `ui_diffpair_set` (atomic per row — diff pairs are
 * small in count, so per-row latency is fine and matches the KiCad
 * UX).
 *
 * "Declare new pair" row at the bottom takes `name + positive +
 * negative + target preset (USB 2.0 90 Ω, LVDS/Ethernet 100 Ω, SATA
 * 85 Ω, PCIe 100 Ω, or custom)`. The same `ui_diffpair_set` tool
 * upserts by name.
 *
 * The panel doesn't touch `Net.diff_pair` back-refs — the server side
 * (`ui_diffpair_set` / `_delete`) propagates those.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useProjectStore } from "../../stores/projectStore";
import type { KcirDiffPair, KcirNet } from "../../stores/projectStore";

/** Common diff-pair impedance presets — surfaced in the new-pair
 * picker. Custom is the implicit "type a number" fallback. */
export const DIFFPAIR_PRESETS: ReadonlyArray<{
  label: string;
  zdiff_ohms: number;
  gap_mm: number;
  skew_mm: number;
}> = [
  { label: "USB 2.0 (90 Ω)", zdiff_ohms: 90, gap_mm: 0.127, skew_mm: 0.127 },
  { label: "LVDS / Ethernet 100Base-TX (100 Ω)", zdiff_ohms: 100, gap_mm: 0.127, skew_mm: 0.127 },
  { label: "PCIe 1.x-3.x (100 Ω)", zdiff_ohms: 100, gap_mm: 0.150, skew_mm: 0.050 },
  { label: "SATA (85 Ω)", zdiff_ohms: 85, gap_mm: 0.150, skew_mm: 0.127 },
];

export interface DiffPairPanelProps {
  projectId: string;
  apiBase?: string;
  fetcher?: typeof fetch;
  className?: string;
  onUpserted?: (pair: KcirDiffPair) => void;
  onDeleted?: (name: string) => void;
}

interface ServerOkUpsert {
  ok: true;
  diff_pair: KcirDiffPair;
}
interface ServerOkDelete {
  ok: true;
  deleted: string;
  cleared_back_refs: string[];
}
interface ServerErr {
  ok?: false;
  error?: string;
  detail?: string;
}

interface DraftRow extends KcirDiffPair {
  /** Internal-only — used to key new (unsaved) rows in React lists. */
  _draftKey?: string;
}

const NEW_ROW_BASE: KcirDiffPair = {
  name: "",
  net_positive: "",
  net_negative: "",
  target_impedance_ohms: 100,
  target_gap_mm: 0.127,
  length_group: "",
  skew_tolerance_mm: 0.127,
};

export function DiffPairPanel(props: DiffPairPanelProps) {
  const { projectId, apiBase = "/api/ui", fetcher, className, onUpserted, onDeleted } = props;

  const project = useProjectStore((s) => s.project);
  const persistedPairs = useMemo<KcirDiffPair[]>(
    () => project?.pcb.diff_pairs ?? [],
    [project],
  );
  const availableNets = useMemo<string[]>(
    () =>
      (project?.pcb.nets ?? [])
        .map((n) => (n as KcirNet).name)
        .filter((n) => n.length > 0),
    [project],
  );
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  // Working copy. We always include the existing pairs + the trailing
  // "new row" sentinel so the UI's "declare new" affordance is always
  // visible. Drafts only persist on Save.
  const initialRows = useMemo<DraftRow[]>(() => persistedPairs.map((p) => ({ ...p })), [persistedPairs]);
  const [rows, setRows] = useState<DraftRow[]>(initialRows);
  const [draft, setDraft] = useState<DraftRow>({ ...NEW_ROW_BASE, _draftKey: "new" });
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(initialRows);
    setDirtyKeys(new Set());
    setError(null);
  }, [initialRows]);

  const updateRow = useCallback((index: number, patch: Partial<KcirDiffPair>) => {
    setRows((prev) => {
      const next = prev.slice();
      const current = next[index];
      if (!current) return prev;
      next[index] = { ...current, ...patch };
      return next;
    });
    const key = rows[index]?.name ?? `<row-${index}>`;
    setDirtyKeys((prev) => {
      if (prev.has(key)) return prev;
      const set = new Set(prev);
      set.add(key);
      return set;
    });
  }, [rows]);

  const applyPreset = useCallback(
    (preset: (typeof DIFFPAIR_PRESETS)[number]) => {
      setDraft((prev) => ({
        ...prev,
        target_impedance_ohms: preset.zdiff_ohms,
        target_gap_mm: preset.gap_mm,
        skew_tolerance_mm: preset.skew_mm,
      }));
    },
    [],
  );

  const post = useCallback(
    async (tool: "ui_diffpair_set" | "ui_diffpair_delete", args: Record<string, unknown>) => {
      const url = `${apiBase}/${tool}/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args }),
      });
      const body = (await resp.json()) as (ServerOkUpsert | ServerOkDelete | ServerErr);
      if (!resp.ok || body.ok !== true) {
        const err = (body as ServerErr).error ?? (body as ServerErr).detail ??
          `${resp.status} ${resp.statusText}`;
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
      const body = (await post("ui_diffpair_set", {
        name: row.name,
        net_positive: row.net_positive,
        net_negative: row.net_negative,
        target_impedance_ohms: row.target_impedance_ohms,
        target_gap_mm: row.target_gap_mm,
        length_group: row.length_group,
        skew_tolerance_mm: row.skew_tolerance_mm,
      })) as ServerOkUpsert;
      onUpserted?.(body.diff_pair);
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
    const key = draft.name || "<new>";
    if (!draft.name.trim()) {
      setError("`name` is required");
      return;
    }
    if (!draft.net_positive || !draft.net_negative) {
      setError("positive and negative nets are required");
      return;
    }
    setBusyKey(key);
    setError(null);
    try {
      const body = (await post("ui_diffpair_set", {
        name: draft.name,
        net_positive: draft.net_positive,
        net_negative: draft.net_negative,
        target_impedance_ohms: draft.target_impedance_ohms,
        target_gap_mm: draft.target_gap_mm,
        length_group: draft.length_group,
        skew_tolerance_mm: draft.skew_tolerance_mm,
      })) as ServerOkUpsert;
      onUpserted?.(body.diff_pair);
      // Append locally so the row shows immediately. Project-store
      // sync from the upstream WS push will replace this view shortly.
      setRows((prev) => [...prev, { ...body.diff_pair }]);
      setDraft({ ...NEW_ROW_BASE, _draftKey: "new" });
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
      await post("ui_diffpair_delete", { name: key });
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
      <div data-testid="diffpair-panel" data-status="empty" className={className} style={panelStyle}>
        <p style={{ padding: 12, color: "#9ca3af", fontSize: 12, margin: 0 }}>No project loaded.</p>
      </div>
    );
  }

  return (
    <div data-testid="diffpair-panel" data-status="ready" className={className} style={panelStyle}>
      <header style={headerStyle}>
        <span style={{ flex: 1 }}>Differential pairs</span>
        <span data-testid="diffpair-count" style={{ color: "#9ca3af", fontSize: 11 }}>
          {rows.length} declared
        </span>
      </header>

      {error ? (
        <div data-testid="diffpair-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}

      <table style={tableStyle}>
        <thead>
          <tr style={{ color: "#9ca3af" }}>
            <th style={thStyle}>Name</th>
            <th style={thStyle}>Net (+)</th>
            <th style={thStyle}>Net (−)</th>
            <th style={thStyle}>Zdiff Ω</th>
            <th style={thStyle}>Gap mm</th>
            <th style={thStyle}>Skew tol mm</th>
            <th style={thStyle}>Length group</th>
            <th style={thStyle}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const key = row.name || `<row-${i}>`;
            const dirty = dirtyKeys.has(key);
            const busy = busyKey === key;
            return (
              <tr
                key={`pair-${i}`}
                data-testid="diffpair-row"
                data-pair-name={row.name}
                data-dirty={dirty ? "true" : "false"}
              >
                <td style={tdStyle}>
                  <input
                    type="text"
                    value={row.name}
                    onChange={(e) => updateRow(i, { name: e.target.value })}
                    style={inputStyle(110)}
                    data-testid="diffpair-name"
                  />
                </td>
                <td style={tdStyle}>{netSelect(row.net_positive, availableNets, (v) => updateRow(i, { net_positive: v }), "diffpair-positive")}</td>
                <td style={tdStyle}>{netSelect(row.net_negative, availableNets, (v) => updateRow(i, { net_negative: v }), "diffpair-negative")}</td>
                <td style={tdStyle}>{numberInput(row.target_impedance_ohms, (v) => updateRow(i, { target_impedance_ohms: v }), "diffpair-zdiff", 70)}</td>
                <td style={tdStyle}>{numberInput(row.target_gap_mm, (v) => updateRow(i, { target_gap_mm: v }), "diffpair-gap", 70)}</td>
                <td style={tdStyle}>{numberInput(row.skew_tolerance_mm, (v) => updateRow(i, { skew_tolerance_mm: v }), "diffpair-skew", 70)}</td>
                <td style={tdStyle}>
                  <input
                    type="text"
                    value={row.length_group}
                    onChange={(e) => updateRow(i, { length_group: e.target.value })}
                    style={inputStyle(110)}
                    data-testid="diffpair-length-group"
                  />
                </td>
                <td style={tdStyle}>
                  <button
                    type="button"
                    onClick={() => void saveRow(i)}
                    disabled={!dirty || busy || !row.name}
                    style={saveButtonStyle(dirty && !busy)}
                    data-testid="diffpair-save"
                  >
                    {busy ? "…" : "Save"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void deleteRow(i)}
                    disabled={busy}
                    style={deleteButtonStyle}
                    data-testid="diffpair-delete"
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div style={newRowSection}>
        <div style={{ ...headerStyle, borderTop: "1px solid #1f2330", background: "#0e1119", paddingLeft: 12 }}>
          <span style={{ flex: 1 }}>Declare new pair</span>
          <select
            value=""
            onChange={(e) => {
              const preset = DIFFPAIR_PRESETS.find((p) => p.label === e.target.value);
              if (preset) applyPreset(preset);
            }}
            data-testid="diffpair-preset"
            style={selectStyle}
          >
            <option value="">Preset…</option>
            {DIFFPAIR_PRESETS.map((p) => (
              <option key={p.label} value={p.label}>{p.label}</option>
            ))}
          </select>
        </div>
        <div style={newRowGrid}>
          <label style={fieldLabel}>
            Name
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              style={inputStyle(140)}
              placeholder="USB_D"
              data-testid="diffpair-new-name"
            />
          </label>
          <label style={fieldLabel}>
            Net (+)
            {netSelect(draft.net_positive, availableNets, (v) => setDraft((d) => ({ ...d, net_positive: v })), "diffpair-new-positive")}
          </label>
          <label style={fieldLabel}>
            Net (−)
            {netSelect(draft.net_negative, availableNets, (v) => setDraft((d) => ({ ...d, net_negative: v })), "diffpair-new-negative")}
          </label>
          <label style={fieldLabel}>
            Zdiff Ω
            {numberInput(draft.target_impedance_ohms, (v) => setDraft((d) => ({ ...d, target_impedance_ohms: v })), "diffpair-new-zdiff", 70)}
          </label>
          <label style={fieldLabel}>
            Gap mm
            {numberInput(draft.target_gap_mm, (v) => setDraft((d) => ({ ...d, target_gap_mm: v })), "diffpair-new-gap", 70)}
          </label>
          <label style={fieldLabel}>
            Skew tol mm
            {numberInput(draft.skew_tolerance_mm, (v) => setDraft((d) => ({ ...d, skew_tolerance_mm: v })), "diffpair-new-skew", 70)}
          </label>
          <label style={fieldLabel}>
            Length group
            <input
              type="text"
              value={draft.length_group}
              onChange={(e) => setDraft((d) => ({ ...d, length_group: e.target.value }))}
              style={inputStyle(140)}
              placeholder="(optional)"
              data-testid="diffpair-new-length-group"
            />
          </label>
          <button
            type="button"
            onClick={() => void declareNew()}
            disabled={busyKey === (draft.name || "<new>")}
            style={addButtonStyle}
            data-testid="diffpair-declare"
          >
            Declare
          </button>
        </div>
      </div>
    </div>
  );
}

function netSelect(
  value: string,
  options: string[],
  onChange: (v: string) => void,
  testId: string,
) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={selectStyle}
      data-testid={testId}
    >
      <option value="">(select)</option>
      {options.map((n) => (
        <option key={n} value={n}>{n}</option>
      ))}
    </select>
  );
}

function numberInput(
  value: number,
  onChange: (v: number) => void,
  testId: string,
  width: number,
) {
  return (
    <input
      type="number"
      step="0.005"
      min="0"
      value={value}
      onChange={(e) => onChange(Number.parseFloat(e.target.value) || 0)}
      style={inputStyle(width)}
      data-testid={testId}
    />
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

const selectStyle: React.CSSProperties = {
  padding: "4px 6px",
  background: "#0d1018",
  color: "#f9fafb",
  border: "1px solid #1f2330",
  borderRadius: 3,
  fontSize: 12,
};

const newRowSection: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const newRowGrid: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
  gap: 8,
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
  alignSelf: "flex-end",
};
