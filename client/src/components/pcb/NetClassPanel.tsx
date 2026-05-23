import { useCallback, useEffect, useMemo, useState } from "react";

import { useProjectStore } from "../../stores/projectStore";

export interface NetClass {
  name: string;
  description?: string;
  clearance_mm: number;
  trace_width_mm: number;
  via_drill_mm: number;
  via_diameter_mm: number;
  diff_pair_width_mm: number | null;
  diff_pair_gap_mm: number | null;
}

/** Default values for a new class — mirrors `ui_netclass_set`. */
export const DEFAULT_NET_CLASS: NetClass = {
  name: "",
  description: "",
  clearance_mm: 0.2,
  trace_width_mm: 0.25,
  via_drill_mm: 0.3,
  via_diameter_mm: 0.6,
  diff_pair_width_mm: null,
  diff_pair_gap_mm: null,
};

export interface NetClassPanelProps {
  projectId: string;
  /** Gateway base URL — defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam. */
  fetcher?: typeof fetch;
  /** Optional className for the outer container. */
  className?: string;
  /** Notify-parent when a class is upserted (post-save). */
  onUpserted?: (cls: NetClass) => void;
  /** Notify-parent when a class is deleted. */
  onDeleted?: (name: string) => void;
}

/**
 * `NetClassPanel` (M2-T-07) — table editor for `pcb.net_classes`.
 *
 * Columns: name · trace width · clearance · via drill · via Ø ·
 * diff-pair width · diff-pair gap · description · row-delete.
 *
 * Editing flow:
 *   - Each row is independent — typing in a numeric input flips
 *     that row to "dirty".
 *   - Pressing `Save` POSTs `ui_netclass_set` for the dirty row's
 *     net class (server side: upsert by name, optionally bind nets).
 *   - The `+ Add` button at the bottom appends a blank row that
 *     becomes a real net class on save.
 *   - Each row's `×` delete button POSTs `ui_netclass_delete`. The
 *     special `Default` class is non-deletable (server enforces).
 *
 * Net binding is part of `ui_netclass_set` (`bind_nets` arg) — the
 * panel exposes a comma-separated input per row so the user can
 * type `+3V3, +5V` and bind a power-rail class in one save.
 *
 * **New nets default to the `Default` class** — the project's net
 * importer wires that fallback up at load time (`projectStore`'s
 * `setProject` doesn't fire here; this panel surfaces and edits
 * the persisted state).
 */
export function NetClassPanel(props: NetClassPanelProps) {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    className,
    onUpserted,
    onDeleted,
  } = props;

  const projectClasses = useProjectStore(
    (s) => s.project?.net_classes ?? null,
  );
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  // Working copy. We never mutate projectStore directly — the
  // backend's POST returns the canonical project which kiserver
  // pushes back through its own update path, and projectStore picks
  // it up the same way the file-open flow does.
  const initial = useMemo<NetClass[]>(() => {
    if (!projectClasses) return [];
    return projectClasses.map((c) => ({
      name: c.name,
      description: "",
      clearance_mm: c.clearance_mm,
      trace_width_mm: c.trace_width_mm,
      via_drill_mm: 0.3,
      via_diameter_mm: 0.6,
      diff_pair_width_mm: null,
      diff_pair_gap_mm: null,
    }));
  }, [projectClasses]);

  const [rows, setRows] = useState<NetClass[]>(initial);
  const [bindInputs, setBindInputs] = useState<Record<string, string>>({});
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Resync when the project changes (file open, undo, etc.).
  useEffect(() => {
    setRows(initial);
    setBindInputs({});
    setDirtyKeys(new Set());
    setError(null);
  }, [initial]);

  const markDirty = useCallback((key: string) => {
    setDirtyKeys((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  }, []);

  const updateRow = useCallback(
    (index: number, patch: Partial<NetClass>) => {
      setRows((prev) => {
        const next = prev.slice();
        const current = next[index];
        if (!current) return prev;
        next[index] = { ...current, ...patch };
        return next;
      });
      const key = rows[index]?.name ?? `<new-${index}>`;
      markDirty(key);
    },
    [markDirty, rows],
  );

  const addRow = useCallback(() => {
    setRows((prev) => [
      ...prev,
      { ...DEFAULT_NET_CLASS, name: `Class_${prev.length + 1}` },
    ]);
    markDirty(`<new-${rows.length}>`);
  }, [markDirty, rows.length]);

  const saveRow = useCallback(
    async (index: number) => {
      const row = rows[index];
      if (!row) return;
      const key = row.name || `<new-${index}>`;
      setBusyKey(key);
      setError(null);
      try {
        const bindRaw = bindInputs[key] ?? "";
        const bindNets = bindRaw
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        const url = `${apiBase}/ui_netclass_set/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            args: {
              name: row.name,
              description: row.description ?? "",
              clearance_mm: row.clearance_mm,
              trace_width_mm: row.trace_width_mm,
              via_drill_mm: row.via_drill_mm,
              via_diameter_mm: row.via_diameter_mm,
              diff_pair_width_mm: row.diff_pair_width_mm,
              diff_pair_gap_mm: row.diff_pair_gap_mm,
              bind_nets: bindNets,
            },
          }),
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          net_class?: NetClass;
          error?: string;
        };
        if (!resp.ok || !body.ok || !body.net_class) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        onUpserted?.(body.net_class);
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
    },
    [apiBase, bindInputs, fetchImpl, onUpserted, projectId, rows],
  );

  const deleteRow = useCallback(
    async (index: number) => {
      const row = rows[index];
      if (!row) return;
      const key = row.name;
      // Locally drop unsaved rows.
      if (dirtyKeys.has(`<new-${index}>`) && !key) {
        setRows((prev) => prev.filter((_, i) => i !== index));
        return;
      }
      if (key === "Default") {
        setError("the `Default` net class cannot be deleted");
        return;
      }
      setBusyKey(key);
      setError(null);
      try {
        const url = `${apiBase}/ui_netclass_delete/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ args: { name: key } }),
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          deleted?: string;
          error?: string;
        };
        if (!resp.ok || !body.ok) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        setRows((prev) => prev.filter((_, i) => i !== index));
        onDeleted?.(key);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyKey(null);
      }
    },
    [apiBase, dirtyKeys, fetchImpl, onDeleted, projectId, rows],
  );

  if (!projectClasses) {
    return (
      <div
        data-testid="net-class-panel"
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
      data-testid="net-class-panel"
      data-status="ready"
      className={className}
      style={panelStyle}
    >
      <header style={headerStyle}>
        <span style={{ flex: 1 }}>Net classes</span>
        <button
          type="button"
          onClick={addRow}
          data-testid="netclass-add"
          style={addButtonStyle}
        >
          + Add
        </button>
      </header>
      {error ? (
        <div data-testid="netclass-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}
      <table style={tableStyle}>
        <thead>
          <tr style={{ color: "#9ca3af" }}>
            <th style={thStyle}>Name</th>
            <th style={thStyle}>Trace mm</th>
            <th style={thStyle}>Clearance mm</th>
            <th style={thStyle}>Via drill mm</th>
            <th style={thStyle}>Via Ø mm</th>
            <th style={thStyle}>Diff-pair w mm</th>
            <th style={thStyle}>Diff-pair gap mm</th>
            <th style={thStyle}>Bind nets (CSV)</th>
            <th style={thStyle}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const key = row.name || `<new-${i}>`;
            const dirty = dirtyKeys.has(key);
            const busy = busyKey === key;
            return (
              <tr
                key={`row-${i}`}
                data-testid="netclass-row"
                data-class-name={row.name}
                data-dirty={dirty ? "true" : "false"}
              >
                <td style={tdStyle}>
                  <input
                    type="text"
                    value={row.name}
                    onChange={(e) => updateRow(i, { name: e.target.value })}
                    style={inputStyle(120)}
                    data-testid="netclass-name"
                    disabled={row.name === "Default"}
                  />
                </td>
                {numericInputs(row, i, updateRow)}
                <td style={tdStyle}>
                  <input
                    type="text"
                    value={bindInputs[key] ?? ""}
                    onChange={(e) =>
                      setBindInputs((prev) => ({ ...prev, [key]: e.target.value }))
                    }
                    style={inputStyle(140)}
                    placeholder="+3V3, +5V"
                    data-testid="netclass-bind"
                  />
                </td>
                <td style={tdStyle}>
                  <button
                    type="button"
                    onClick={() => void saveRow(i)}
                    disabled={!dirty || busy || !row.name}
                    style={saveButtonStyle(dirty && !busy)}
                    data-testid="netclass-save"
                  >
                    {busy ? "…" : "Save"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void deleteRow(i)}
                    disabled={busy}
                    style={deleteButtonStyle}
                    data-testid="netclass-delete"
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function numericInputs(
  row: NetClass,
  index: number,
  updateRow: (index: number, patch: Partial<NetClass>) => void,
) {
  const cells: Array<{
    key: keyof NetClass;
    width: number;
    nullable?: boolean;
    testid: string;
  }> = [
    { key: "trace_width_mm", width: 70, testid: "netclass-trace-width" },
    { key: "clearance_mm", width: 70, testid: "netclass-clearance" },
    { key: "via_drill_mm", width: 70, testid: "netclass-via-drill" },
    { key: "via_diameter_mm", width: 70, testid: "netclass-via-dia" },
    {
      key: "diff_pair_width_mm",
      width: 80,
      nullable: true,
      testid: "netclass-dp-width",
    },
    {
      key: "diff_pair_gap_mm",
      width: 80,
      nullable: true,
      testid: "netclass-dp-gap",
    },
  ];
  return cells.map((c) => {
    const raw = row[c.key] as number | null;
    const value = raw == null ? "" : String(raw);
    return (
      <td key={String(c.key)} style={tdStyle}>
        <input
          type="number"
          step="0.05"
          min="0"
          value={value}
          onChange={(e) => {
            const next = e.target.value;
            const parsed = next === "" ? null : Number.parseFloat(next);
            updateRow(index, {
              [c.key]: c.nullable
                ? parsed
                : Number.isFinite(parsed) ? (parsed as number) : 0,
            } as Partial<NetClass>);
          }}
          style={inputStyle(c.width)}
          data-testid={c.testid}
        />
      </td>
    );
  });
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
  gap: 6,
  padding: "8px 12px",
  borderBottom: "1px solid #1f2330",
  fontWeight: 600,
  color: "#cbd5e1",
  letterSpacing: 0.4,
  textTransform: "uppercase",
  background: "#161b25",
};

const addButtonStyle: React.CSSProperties = {
  padding: "4px 10px",
  background: "#1e40af",
  color: "#f9fafb",
  border: "none",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 11,
};

const errorRowStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "rgba(255, 77, 79, 0.15)",
  color: "#ff7875",
  fontSize: 11,
  borderBottom: "1px solid #401b1b",
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
