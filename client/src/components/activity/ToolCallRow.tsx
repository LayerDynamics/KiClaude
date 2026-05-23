/**
 * `ToolCallRow` — one row in the M1-T-08 ActivityJournal. Renders
 * a single `kc_*` / `ui_*` mutating tool call: timestamp, tool
 * name, status pill, optional snapshot id, and (when the call
 * mutated state) a "Revert" button that POSTs to
 * `/api/server/project/{id}/snapshot/revert`.
 *
 * Pure presentation — all state lives in `activityStore`. The
 * revert callback is injected so the journal can centralise
 * fetch/error handling.
 */

import { useCallback, useState } from "react";

import type { ActivityEntry } from "../../stores/activityStore";
import { Card } from "../UI";

export interface ToolCallRowProps {
  entry: ActivityEntry;
  /** Async callback invoked when the user clicks "Revert". Receives
   * the entry; should call the snapshot-revert endpoint and update
   * `activityStore` via `markReverted` on success. */
  onRevert?: (entry: ActivityEntry) => Promise<void>;
  /** Optional callback when the user toggles the JSON expansion. */
  onToggleExpanded?: (id: string, expanded: boolean) => void;
}

const STATUS_PILL_CLASS: Record<ActivityEntry["status"], string> = {
  running: "bg-blue-100 text-blue-800",
  ok: "bg-green-100 text-green-800",
  error: "bg-red-100 text-red-800",
  denied: "bg-yellow-100 text-yellow-800",
};

const STATUS_CARD_TONE: Record<ActivityEntry["status"], "default" | "danger"> = {
  running: "default",
  ok: "default",
  error: "danger",
  denied: "default",
};

function formatTs(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  // HH:MM:SS.mmm in local time — matches the chat tool card.
  return date.toISOString().slice(11, 23);
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function ToolCallRow({ entry, onRevert, onToggleExpanded }: ToolCallRowProps) {
  const [expanded, setExpanded] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [revertError, setRevertError] = useState<string | null>(null);

  const toggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      onToggleExpanded?.(entry.id, next);
      return next;
    });
  }, [entry.id, onToggleExpanded]);

  const handleRevert = useCallback(async () => {
    if (!onRevert || reverting || entry.reverted) return;
    setReverting(true);
    setRevertError(null);
    try {
      await onRevert(entry);
    } catch (err) {
      setRevertError(err instanceof Error ? err.message : String(err));
    } finally {
      setReverting(false);
    }
  }, [entry, onRevert, reverting]);

  const canRevert =
    entry.mutating &&
    !entry.reverted &&
    Boolean(entry.snapshot_id) &&
    entry.status !== "running" &&
    entry.status !== "denied";

  return (
    <Card
      tone={STATUS_CARD_TONE[entry.status]}
      flush
      data-testid={`activity-row-${entry.id}`}
      data-status={entry.status}
      data-reverted={entry.reverted ? "true" : "false"}
      className="mb-1.5 font-mono text-xs"
    >
      <div className="px-2.5 py-2">
        <div className="flex items-center gap-2">
          <span
            data-testid={`activity-status-${entry.id}`}
            className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${STATUS_PILL_CLASS[entry.status]}`}
          >
            {entry.status}
          </span>
          <span className="text-[var(--text)]/70">{formatTs(entry.ts)}</span>
          <span className="font-semibold text-[var(--text-h)]">
            {entry.tool_name}
          </span>
          {entry.duration_ms !== undefined ? (
            <span className="text-[var(--text)]/60">
              {entry.duration_ms.toFixed(0)}ms
            </span>
          ) : null}
          <span className="flex-1" />
          {canRevert ? (
            <button
              type="button"
              data-testid={`activity-revert-${entry.id}`}
              disabled={reverting}
              onClick={handleRevert}
              className={`inline-flex h-6 items-center rounded border border-[var(--border)] px-2 text-[11px] ${
                reverting
                  ? "cursor-default bg-[var(--code-bg)]"
                  : "cursor-pointer bg-[var(--bg)] hover:bg-[var(--code-bg)]"
              }`}
            >
              {reverting ? "Reverting…" : "Revert"}
            </button>
          ) : null}
          {entry.reverted ? (
            <span
              data-testid={`activity-reverted-${entry.id}`}
              className="text-[11px] italic text-[var(--text)]/60"
            >
              reverted
            </span>
          ) : null}
          <button
            type="button"
            data-testid={`activity-toggle-${entry.id}`}
            onClick={toggle}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-[var(--border)] bg-[var(--code-bg)] text-[11px] text-[var(--text-h)] hover:bg-[var(--bg)]"
          >
            {expanded ? "−" : "+"}
          </button>
        </div>
        {revertError ? (
          <div
            data-testid={`activity-revert-error-${entry.id}`}
            className="mt-1 text-[11px] text-red-600"
          >
            revert failed: {revertError}
          </div>
        ) : null}
        {expanded ? (
          <div className="mt-1.5 grid gap-1.5">
            {entry.snapshot_id ? (
              <div className="text-[11px] text-[var(--text)]/70">
                snapshot:{" "}
                <span className="text-[var(--text-h)]">{entry.snapshot_id}</span>
              </div>
            ) : null}
            {entry.input ? (
              <div>
                <div className="text-[11px] text-[var(--text)]/70">input</div>
                <pre
                  data-testid={`activity-input-${entry.id}`}
                  className="m-0 rounded bg-[var(--code-bg)] p-1.5 text-[11px] break-words whitespace-pre-wrap"
                >
                  {formatJson(entry.input)}
                </pre>
              </div>
            ) : null}
            {entry.output ? (
              <div>
                <div className="text-[11px] text-[var(--text)]/70">output</div>
                <pre
                  data-testid={`activity-output-${entry.id}`}
                  className="m-0 rounded bg-[var(--code-bg)] p-1.5 text-[11px] break-words whitespace-pre-wrap"
                >
                  {formatJson(entry.output)}
                </pre>
              </div>
            ) : null}
            {entry.error ? (
              <div
                data-testid={`activity-error-${entry.id}`}
                className="text-[11px] text-red-600"
              >
                {entry.error}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
