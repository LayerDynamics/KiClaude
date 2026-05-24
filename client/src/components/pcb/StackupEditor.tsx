/**
 * `StackupEditor` (M3-T-01) — table editor for `project.stackup.layers`.
 *
 * Columns: name · kind · thickness (mm) · Er · loss-tangent · material/color · move ▲▼ · ×
 *
 * Editing model is whole-payload-on-save, matching `kc_mcp.ui_tools.stackup_edit`:
 * the user freely edits / inserts / removes / reorders layers in a
 * local working copy; clicking **Save** POSTs the whole payload to
 * `ui_stackup_set` and the kiserver replaces `project.stackup`
 * atomically. This matches how KiCad's own stack-manager dialog works
 * — modal, commit-on-OK — and lets server-side validate the
 * `F.Cu`-first / `B.Cu`-last invariant once instead of on every
 * keystroke.
 *
 * The board thickness shown in the header is read from the canonical
 * server payload (sum of layer thicknesses, recomputed on save).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useProjectStore } from "../../stores/projectStore";
import type { KcirStackup, KcirStackupLayer } from "../../stores/projectStore";

/** The kinds the validator on the Python side accepts (mirror of
 * `kc_mcp.ui_tools.stackup_edit.ALLOWED_KINDS`). Keep in lock-step. */
export const STACKUP_KINDS: readonly KcirStackupLayer["kind"][] = [
  "copper",
  "dielectric",
  "soldermask",
  "silkscreen",
  "paste",
  "adhesive",
];

/** Common board finishes — typed into the panel as a free-form input
 * with a datalist suggestion. KiCad accepts arbitrary strings. */
const FINISH_SUGGESTIONS = ["HASL", "ENIG", "OSP", "Immersion Silver", "HASL-LF"];

const DEFAULT_LAYER: KcirStackupLayer = {
  name: "",
  kind: "dielectric",
  thickness_mm: 0.2,
  dielectric_constant: 4.5,
  loss_tangent: 0.02,
  color: "FR4",
};

const DEFAULT_STACKUP: KcirStackup = {
  layers: [],
  power_plane_layers: [],
  controlled_impedance: false,
  board_thickness_mm: 0,
  finish: "HASL",
};

export interface StackupEditorProps {
  projectId: string;
  /** Gateway base URL — defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam — defaults to `globalThis.fetch`. */
  fetcher?: typeof fetch;
  className?: string;
  /** Notify-parent after a successful save. */
  onSaved?: (stackup: KcirStackup) => void;
}

interface ServerResponse {
  ok?: boolean;
  stackup?: KcirStackup;
  error?: string;
  detail?: string;
}

export function StackupEditor(props: StackupEditorProps) {
  const { projectId, apiBase = "/api/ui", fetcher, className, onSaved } = props;
  const projectStackup = useProjectStore((s) => s.project?.stackup ?? null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  // Working copy. Resynced when the project's stackup changes (file
  // open, save commit, undo) so the panel always reflects truth.
  const initial = useMemo<KcirStackup>(
    () => projectStackup ?? DEFAULT_STACKUP,
    [projectStackup],
  );
  const [stackup, setStackup] = useState<KcirStackup>(initial);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStackup(initial);
    setDirty(false);
    setError(null);
  }, [initial]);

  const markDirty = useCallback(() => {
    setDirty(true);
    setError(null);
  }, []);

  const updateLayer = useCallback(
    (index: number, patch: Partial<KcirStackupLayer>) => {
      setStackup((prev) => {
        const layers = prev.layers.slice();
        const current = layers[index];
        if (!current) return prev;
        layers[index] = { ...current, ...patch };
        return { ...prev, layers };
      });
      markDirty();
    },
    [markDirty],
  );

  const insertLayer = useCallback(() => {
    setStackup((prev) => ({
      ...prev,
      layers: [
        ...prev.layers,
        {
          ...DEFAULT_LAYER,
          name: `dielectric ${prev.layers.filter((l) => l.kind === "dielectric").length + 1}`,
        },
      ],
    }));
    markDirty();
  }, [markDirty]);

  const deleteLayer = useCallback(
    (index: number) => {
      setStackup((prev) => ({
        ...prev,
        layers: prev.layers.filter((_, i) => i !== index),
      }));
      markDirty();
    },
    [markDirty],
  );

  const moveLayer = useCallback(
    (index: number, direction: -1 | 1) => {
      setStackup((prev) => {
        const target = index + direction;
        if (target < 0 || target >= prev.layers.length) return prev;
        const layers = prev.layers.slice();
        const tmp = layers[index];
        const swap = layers[target];
        if (!tmp || !swap) return prev;
        layers[index] = swap;
        layers[target] = tmp;
        return { ...prev, layers };
      });
      markDirty();
    },
    [markDirty],
  );

  const updateField = useCallback(
    <K extends keyof KcirStackup>(key: K, value: KcirStackup[K]) => {
      setStackup((prev) => ({ ...prev, [key]: value }));
      markDirty();
    },
    [markDirty],
  );

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const url = `${apiBase}/ui_stackup_set/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          args: {
            layers: stackup.layers,
            finish: stackup.finish,
            controlled_impedance: stackup.controlled_impedance,
            power_plane_layers: stackup.power_plane_layers,
          },
        }),
      });
      const body = (await resp.json()) as ServerResponse;
      if (!resp.ok || !body.ok || !body.stackup) {
        const detail = body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`;
        throw new Error(detail);
      }
      setStackup(body.stackup);
      setDirty(false);
      onSaved?.(body.stackup);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [apiBase, fetchImpl, onSaved, projectId, stackup]);

  const revert = useCallback(() => {
    setStackup(initial);
    setDirty(false);
    setError(null);
  }, [initial]);

  // Derived: live board thickness while editing (server recomputes on save).
  const liveThicknessMm = useMemo(
    () => stackup.layers.reduce((sum, l) => sum + (Number(l.thickness_mm) || 0), 0),
    [stackup.layers],
  );

  if (!projectStackup) {
    return (
      <div
        data-testid="stackup-editor"
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
      data-testid="stackup-editor"
      data-status="ready"
      data-dirty={dirty ? "true" : "false"}
      className={className}
      style={panelStyle}
    >
      <header style={headerStyle}>
        <span style={{ flex: 1 }}>Stackup</span>
        <span data-testid="stackup-board-thickness" style={{ color: "#9ca3af", fontSize: 11 }}>
          {liveThicknessMm.toFixed(3)} mm total
        </span>
        <button
          type="button"
          onClick={revert}
          disabled={!dirty || busy}
          style={revertButtonStyle(dirty && !busy)}
          data-testid="stackup-revert"
        >
          Revert
        </button>
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || busy}
          style={saveButtonStyle(dirty && !busy)}
          data-testid="stackup-save"
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </header>

      {error ? (
        <div data-testid="stackup-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}

      <table style={tableStyle}>
        <thead>
          <tr style={{ color: "#9ca3af" }}>
            <th style={thStyle}>#</th>
            <th style={thStyle}>Name</th>
            <th style={thStyle}>Kind</th>
            <th style={thStyle}>Thickness mm</th>
            <th style={thStyle}>εr</th>
            <th style={thStyle}>Loss tan δ</th>
            <th style={thStyle}>Material / color</th>
            <th style={thStyle}></th>
          </tr>
        </thead>
        <tbody>
          {stackup.layers.map((layer, i) => (
            <tr
              key={`layer-${i}`}
              data-testid="stackup-row"
              data-layer-name={layer.name}
              data-layer-kind={layer.kind}
            >
              <td style={tdStyle}>{i + 1}</td>
              <td style={tdStyle}>
                <input
                  type="text"
                  value={layer.name}
                  onChange={(e) => updateLayer(i, { name: e.target.value })}
                  style={inputStyle(120)}
                  data-testid="stackup-name"
                />
              </td>
              <td style={tdStyle}>
                <select
                  value={layer.kind}
                  onChange={(e) => updateLayer(i, { kind: e.target.value })}
                  style={selectStyle}
                  data-testid="stackup-kind"
                >
                  {STACKUP_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </select>
              </td>
              <td style={tdStyle}>
                <input
                  type="number"
                  step="0.005"
                  min="0"
                  value={layer.thickness_mm}
                  onChange={(e) =>
                    updateLayer(i, { thickness_mm: Number.parseFloat(e.target.value) || 0 })
                  }
                  style={inputStyle(80)}
                  data-testid="stackup-thickness"
                />
              </td>
              <td style={tdStyle}>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  value={layer.dielectric_constant ?? ""}
                  onChange={(e) => {
                    const raw = e.target.value;
                    const parsed = raw === "" ? null : Number.parseFloat(raw);
                    updateLayer(i, {
                      dielectric_constant: Number.isFinite(parsed) ? parsed : null,
                    });
                  }}
                  disabled={layer.kind === "copper"}
                  placeholder={layer.kind === "copper" ? "—" : ""}
                  style={inputStyle(60)}
                  data-testid="stackup-epsilon"
                />
              </td>
              <td style={tdStyle}>
                <input
                  type="number"
                  step="0.001"
                  min="0"
                  value={layer.loss_tangent ?? ""}
                  onChange={(e) => {
                    const raw = e.target.value;
                    const parsed = raw === "" ? null : Number.parseFloat(raw);
                    updateLayer(i, {
                      loss_tangent: Number.isFinite(parsed) ? parsed : null,
                    });
                  }}
                  disabled={layer.kind === "copper"}
                  placeholder={layer.kind === "copper" ? "—" : ""}
                  style={inputStyle(70)}
                  data-testid="stackup-losstangent"
                />
              </td>
              <td style={tdStyle}>
                <input
                  type="text"
                  value={layer.color}
                  onChange={(e) => updateLayer(i, { color: e.target.value })}
                  style={inputStyle(100)}
                  data-testid="stackup-material"
                />
              </td>
              <td style={tdStyle}>
                <button
                  type="button"
                  onClick={() => moveLayer(i, -1)}
                  disabled={i === 0 || busy}
                  style={moveButtonStyle}
                  data-testid="stackup-move-up"
                  aria-label="Move layer up"
                >
                  ▲
                </button>
                <button
                  type="button"
                  onClick={() => moveLayer(i, 1)}
                  disabled={i === stackup.layers.length - 1 || busy}
                  style={moveButtonStyle}
                  data-testid="stackup-move-down"
                  aria-label="Move layer down"
                >
                  ▼
                </button>
                <button
                  type="button"
                  onClick={() => deleteLayer(i)}
                  disabled={busy}
                  style={deleteButtonStyle}
                  data-testid="stackup-delete"
                  aria-label="Delete layer"
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={footerStyle}>
        <button
          type="button"
          onClick={insertLayer}
          disabled={busy}
          style={addButtonStyle}
          data-testid="stackup-add"
        >
          + Add layer
        </button>
        <label style={fieldLabelStyle}>
          Finish:
          <input
            type="text"
            list="stackup-finish-options"
            value={stackup.finish}
            onChange={(e) => updateField("finish", e.target.value)}
            style={inputStyle(120)}
            data-testid="stackup-finish"
          />
          <datalist id="stackup-finish-options">
            {FINISH_SUGGESTIONS.map((f) => (
              <option key={f} value={f} />
            ))}
          </datalist>
        </label>
        <label style={fieldLabelStyle}>
          <input
            type="checkbox"
            checked={stackup.controlled_impedance}
            onChange={(e) => updateField("controlled_impedance", e.target.checked)}
            data-testid="stackup-controlled-impedance"
          />
          Controlled impedance
        </label>
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

function saveButtonStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 12px",
    background: active ? "#1e40af" : "#1f2937",
    color: active ? "#f9fafb" : "#9ca3af",
    border: "none",
    borderRadius: 3,
    cursor: active ? "pointer" : "default",
    fontSize: 11,
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

const addButtonStyle: React.CSSProperties = {
  padding: "4px 10px",
  background: "#1e40af",
  color: "#f9fafb",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
};

const moveButtonStyle: React.CSSProperties = {
  padding: "2px 6px",
  background: "transparent",
  color: "#9ca3af",
  border: "1px solid #1f2330",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
  marginRight: 2,
};

const deleteButtonStyle: React.CSSProperties = {
  padding: "2px 6px",
  background: "transparent",
  color: "#ff7875",
  border: "1px solid #401b1b",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 12,
  marginLeft: 4,
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "8px 12px",
  borderTop: "1px solid #1f2330",
  background: "#0e1119",
  color: "#cbd5e1",
};

const fieldLabelStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: 11,
};
