import { useEffect, useMemo, useState } from "react";

import { Input, Panel, Text } from "../UI";

export interface SymbolForPanel {
  uuid: string;
  refdes: string;
  value: string;
  lib_id: string;
  footprint: string;
  mpn: string;
  datasheet: string;
  is_power_symbol?: boolean;
}

export interface PropertyPanelProps {
  /** Currently-selected symbol. `null` collapses the panel. */
  symbol: SymbolForPanel | null;
  /** Active project — required to dispatch the edit. */
  projectId: string;
  /** Optional allowed footprint values for the picker. Validation
   *  rejects values not in this list (mirrors `fp-lib-table`). When
   *  omitted, footprint accepts any non-empty string. */
  footprintCandidates?: string[];
  /** Gateway base path. Defaults to `/api/ui`. */
  apiBase?: string;
  /** Test seam. */
  fetcher?: typeof fetch;
  /** Notify parent when a save round-trips through the gateway. */
  onSaved?: (updated: SymbolForPanel, changedFields: string[]) => void;
  /** Notify parent when the user closes the panel. */
  onClose?: () => void;
}

/**
 * `PropertyPanel` (M1-T-04) — the side-panel form the React
 * schematic editor renders when the user selects a symbol. The
 * form's Save button POSTs to `/api/ui/ui_symbol_edit_props` and
 * relays the gateway's `changed_fields` back to the parent so the
 * undo journal can record it.
 */
export function PropertyPanel(props: PropertyPanelProps) {
  const {
    symbol,
    projectId,
    footprintCandidates,
    apiBase = "/api/ui",
    fetcher,
    onSaved,
    onClose,
  } = props;

  const [draft, setDraft] = useState<SymbolForPanel | null>(symbol);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const fetchImpl = useMemo(
    () => fetcher ?? globalThis.fetch.bind(globalThis),
    [fetcher],
  );

  // Sync the form whenever the selected symbol changes.
  useEffect(() => {
    setDraft(symbol);
    setError(null);
    setSavedAt(null);
  }, [symbol]);

  if (!symbol || !draft) return null;

  const candidateSet = useMemo(
    () => new Set((footprintCandidates ?? []).map((s) => s.toLowerCase())),
    [footprintCandidates],
  );

  function update<K extends keyof SymbolForPanel>(key: K, value: SymbolForPanel[K]) {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function validate(d: SymbolForPanel): string | null {
    if (!d.refdes.trim()) return "Refdes is required";
    if (d.footprint && footprintCandidates && footprintCandidates.length > 0) {
      if (!candidateSet.has(d.footprint.toLowerCase())) {
        return `Footprint "${d.footprint}" is not in fp-lib-table`;
      }
    }
    if (d.datasheet) {
      try {
        // Accept either a URL or a relative path; URL constructor
        // rejects garbage like "not a url".
        if (/^[a-z]+:\/\//i.test(d.datasheet)) {
          new URL(d.datasheet);
        }
      } catch {
        return `Datasheet "${d.datasheet}" is not a valid URL`;
      }
    }
    return null;
  }

  async function save() {
    if (!draft) return;
    const validationError = validate(draft);
    if (validationError) {
      setError(validationError);
      return;
    }
    setPending(true);
    setError(null);
    try {
      const url = `${apiBase}/ui_symbol_edit_props/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          args: {
            symbol_uuid: draft.uuid,
            refdes: draft.refdes,
            value: draft.value,
            footprint: draft.footprint,
            mpn: draft.mpn,
            datasheet: draft.datasheet,
          },
        }),
      });
      const body = (await resp.json()) as {
        ok?: boolean;
        changed_fields?: string[];
        error?: string;
      };
      if (!resp.ok || !body.ok) {
        throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
      }
      onSaved?.(draft, body.changed_fields ?? []);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel
      data-testid="property-panel"
      aria-label="kiclaude symbol property panel"
      density="compact"
      title={
        <div className="flex min-w-0 items-baseline gap-2">
          <Text variant="h4">Symbol</Text>
          <Text variant="caption" className="truncate">
            {draft.lib_id}
          </Text>
        </div>
      }
      actions={
        <button
          data-testid="property-panel-close"
          type="button"
          onClick={onClose}
          aria-label="close"
          className="rounded p-1 text-lg leading-none text-[var(--text)]/70 hover:bg-[var(--code-bg)] hover:text-[var(--text-h)]"
        >
          ×
        </button>
      }
      className="w-80"
    >
      <div className="flex flex-col gap-3">
        <Input
          inputSize="sm"
          label="Refdes"
          data-testid="property-refdes"
          value={draft.refdes}
          onChange={(e) => update("refdes", e.target.value)}
        />
        <Input
          inputSize="sm"
          label="Value"
          data-testid="property-value"
          value={draft.value}
          onChange={(e) => update("value", e.target.value)}
        />
        <FootprintPicker
          value={draft.footprint}
          candidates={footprintCandidates}
          onChange={(v) => update("footprint", v)}
        />
        <Input
          inputSize="sm"
          label="MPN"
          data-testid="property-mpn"
          value={draft.mpn}
          onChange={(e) => update("mpn", e.target.value)}
        />
        <Input
          inputSize="sm"
          label="Datasheet"
          data-testid="property-datasheet"
          value={draft.datasheet}
          onChange={(e) => update("datasheet", e.target.value)}
        />
        {error ? (
          <p
            data-testid="property-error"
            className="m-0 text-xs text-red-600 dark:text-red-400"
          >
            {error}
          </p>
        ) : null}
        {savedAt && !error ? (
          <p
            data-testid="property-saved"
            className="m-0 text-xs text-emerald-600 dark:text-emerald-400"
          >
            Saved.
          </p>
        ) : null}
        <button
          type="button"
          data-testid="property-save"
          onClick={save}
          disabled={pending}
          className={`mt-1 inline-flex h-8 items-center justify-center rounded-md px-3 text-sm font-semibold text-white transition-colors ${
            pending
              ? "cursor-wait bg-[var(--text)]/50"
              : "cursor-pointer bg-[var(--accent)] hover:opacity-90"
          }`}
        >
          {pending ? "Saving…" : "Save"}
        </button>
      </div>
    </Panel>
  );
}

function FootprintPicker(props: {
  value: string;
  candidates?: string[];
  onChange: (v: string) => void;
}) {
  if (!props.candidates || props.candidates.length === 0) {
    return (
      <Input
        inputSize="sm"
        label="Footprint"
        data-testid="property-footprint"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
      />
    );
  }
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium uppercase tracking-wide text-[var(--text)] opacity-80">
        Footprint
      </span>
      <select
        data-testid="property-footprint"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="h-7 w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 text-sm text-[var(--text-h)] outline-none transition-colors focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent)]/30"
      >
        <option value="">(none)</option>
        {props.candidates.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </label>
  );
}
