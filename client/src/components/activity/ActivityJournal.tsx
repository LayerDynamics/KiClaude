/**
 * `ActivityJournal` (M1-T-08) — chronological, persisted log of every
 * mutating MCP tool call. Subscribes to the gateway WebSocket for
 * `tool_use_start` / `tool_use_end` / `tool_revert` frames and shows
 * a row per call, each with its own "Revert" button.
 *
 * SPEC refs: FR-056 (per-call revert).
 *
 * Persistence: rides on the `activityStore` zustand `persist`
 * middleware so a page reload keeps the journal — the spec requires
 * this so the user can revert a mutation across reload boundaries.
 */

import { useCallback, useEffect, useMemo, useRef } from "react";

import {
  type ActivityEntry,
  type ActivityStatus,
  isMutatingTool,
  useActivityStore,
} from "../../stores/activityStore";
import { KiclaudeWsClient, type KiclaudeWsListener } from "../../lib/ws";
import { Panel, Text } from "../UI";
import { ToolCallRow } from "./ToolCallRow";

export interface ActivityJournalProps {
  /** WS client to subscribe to. The parent typically constructs one
   * per session; the journal does NOT own the lifecycle. */
  client: KiclaudeWsClient;
  /** Optional override for the gateway base URL. Used by the revert
   * `fetch`. Defaults to `/api/server`. */
  apiBase?: string;
  /** Active project id — passed to the revert endpoint. May be null
   * before the user opens a project. */
  projectId?: string | null;
  /** Test seam for the revert HTTP call. */
  fetcher?: typeof fetch;
  /** When true, the journal also tracks non-mutating calls (for
   * debugging). Defaults to false — the spec says only mutations
   * appear in the journal. */
  trackReadOnly?: boolean;
}

interface ToolUseStartFrame {
  kind: "tool_use_start";
  id: string;
  tool_name: string;
  input?: Record<string, unknown>;
  snapshot_id?: string;
  project_id?: string | null;
  session_id?: string;
  mutating?: boolean;
  ts?: string;
}

interface ToolUseEndFrame {
  kind: "tool_use_end";
  id: string;
  ok?: boolean;
  duration_ms?: number;
  output?: Record<string, unknown>;
  error?: string;
  ts?: string;
}

interface ToolRevertFrame {
  kind: "tool_revert";
  id: string;
}

type JournalFrame = ToolUseStartFrame | ToolUseEndFrame | ToolRevertFrame;

function isToolUseStart(v: unknown): v is ToolUseStartFrame {
  return (
    typeof v === "object" &&
    v !== null &&
    (v as { kind?: unknown }).kind === "tool_use_start"
  );
}
function isToolUseEnd(v: unknown): v is ToolUseEndFrame {
  return (
    typeof v === "object" &&
    v !== null &&
    (v as { kind?: unknown }).kind === "tool_use_end"
  );
}
function isToolRevert(v: unknown): v is ToolRevertFrame {
  return (
    typeof v === "object" &&
    v !== null &&
    (v as { kind?: unknown }).kind === "tool_revert"
  );
}

export function ActivityJournal({
  client,
  apiBase = "/api/server",
  projectId = null,
  fetcher,
  trackReadOnly = false,
}: ActivityJournalProps) {
  const entries = useActivityStore((s) => s.entries);
  const append = useActivityStore((s) => s.append);
  const finalize = useActivityStore((s) => s.finalize);
  const markReverted = useActivityStore((s) => s.markReverted);
  const clear = useActivityStore((s) => s.clear);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);
  const fetchRef = useRef(fetchImpl);
  fetchRef.current = fetchImpl;
  const projectRef = useRef<string | null>(projectId);
  projectRef.current = projectId;
  const baseRef = useRef(apiBase);
  baseRef.current = apiBase;
  const trackReadOnlyRef = useRef(trackReadOnly);
  trackReadOnlyRef.current = trackReadOnly;

  useEffect(() => {
    const listener: KiclaudeWsListener = (event) => {
      if (event.kind !== "json") return;
      const data = event.data as JournalFrame | undefined;
      if (!data) return;
      if (isToolUseStart(data)) {
        const mutating = data.mutating ?? isMutatingTool(data.tool_name);
        if (!mutating && !trackReadOnlyRef.current) return;
        const entry: ActivityEntry = {
          id: data.id,
          ts: data.ts ?? new Date().toISOString(),
          tool_name: data.tool_name,
          mutating,
          status: "running",
          input: data.input,
          snapshot_id: data.snapshot_id,
          project_id: data.project_id ?? projectRef.current,
          session_id: data.session_id,
        };
        append(entry);
        return;
      }
      if (isToolUseEnd(data)) {
        const status: ActivityStatus = data.ok === false ? "error" : "ok";
        finalize({
          id: data.id,
          status,
          duration_ms: data.duration_ms,
          output: data.output,
          error: data.error,
          ts: data.ts,
        });
        return;
      }
      if (isToolRevert(data)) {
        markReverted(data.id);
      }
    };
    return client.subscribe(listener);
  }, [append, client, finalize, markReverted]);

  const handleRevert = useCallback(
    async (entry: ActivityEntry) => {
      const pid = entry.project_id ?? projectRef.current;
      const snapId = entry.snapshot_id;
      if (!pid || !snapId) {
        throw new Error(
          `missing ${pid ? "snapshot_id" : "project_id"} on entry ${entry.id}`,
        );
      }
      const url = `${baseRef.current}/project/${encodeURIComponent(
        pid,
      )}/snapshot/revert`;
      const resp = await fetchRef.current(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ snapshot_id: snapId }),
      });
      const body = (await resp.json()) as {
        ok?: boolean;
        error?: string;
        detail?: string;
        reverted_to_label?: string;
      };
      if (!resp.ok || body.ok === false) {
        throw new Error(
          body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`,
        );
      }
      markReverted(entry.id);
    },
    [markReverted],
  );

  const visible = useMemo(() => {
    return entries
      .filter((e) => trackReadOnly || e.mutating)
      .slice()
      .reverse();
  }, [entries, trackReadOnly]);

  return (
    <Panel
      data-testid="activity-journal"
      density="compact"
      className="h-full"
      title={
        <div className="flex items-baseline gap-2">
          <Text variant="h4">Activity</Text>
          <Text variant="caption">
            {visible.length} {visible.length === 1 ? "call" : "calls"}
          </Text>
        </div>
      }
      actions={
        <button
          type="button"
          data-testid="activity-clear"
          onClick={clear}
          className="inline-flex h-6 items-center rounded border border-[var(--border)] bg-[var(--bg)] px-2 text-[11px] text-[var(--text-h)] hover:bg-[var(--code-bg)]"
        >
          Clear
        </button>
      }
    >
      {visible.length === 0 ? (
        <div
          data-testid="activity-empty"
          className="px-1 py-3 text-xs text-[var(--text)]/60"
        >
          No tool calls yet.
        </div>
      ) : (
        <div data-testid="activity-list" className="flex flex-col gap-1">
          {visible.map((entry) => (
            <ToolCallRow
              key={entry.id}
              entry={entry}
              onRevert={handleRevert}
            />
          ))}
        </div>
      )}
    </Panel>
  );
}
