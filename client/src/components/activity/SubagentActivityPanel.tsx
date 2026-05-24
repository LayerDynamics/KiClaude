/**
 * `SubagentActivityPanel` (M3-T-09) — live view of the agent
 * service's session + tool-call lifecycle, grouped by subagent.
 *
 * Data path:
 *
 *   agent.hooks.lifecycle ──record──▶ agent.activity.registry
 *   GET /activity/snapshot?since=N (gateway proxies to agent :8082)
 *   ──▶ panel polls every `pollIntervalMs` (default 1000)
 *
 * Layout:
 *
 *   - One card per session, ordered by `started_at`.
 *   - Orchestrator sessions render at depth 0; subagent sessions
 *     (with `parent_session_id` set) nest under their parent.
 *   - Each session card lists its tool calls with status pills
 *     (running / ok / error) and timing.
 *   - Header shows running totals: active sessions, running calls,
 *     errors.
 *
 * The poll deliberately does NOT request via WebSocket — the agent's
 * `/ws` surface is reserved for chat/tool-use frames. A 1-second poll
 * is well below human-eye refresh, and the `since=seq` filter keeps
 * each tick to just the changed rows.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface ActivitySessionRecord {
  session_id: string;
  agent_id: string;
  parent_session_id: string | null;
  started_at: string;
  ended_at: string | null;
  seq: number;
}

export interface ActivityCallRecord {
  tool_use_id: string;
  session_id: string;
  tool_name: string;
  project_id: string | null;
  started_at: string;
  ended_at: string | null;
  ok: boolean | null;
  duration_ms: number | null;
  status: "running" | "ok" | "error";
  seq: number;
}

interface SnapshotResponse {
  ok?: boolean;
  sessions?: ActivitySessionRecord[];
  calls?: ActivityCallRecord[];
  high_water_seq?: number;
  error?: string;
  detail?: string;
}

export interface SubagentActivityPanelProps {
  /** Gateway base URL — defaults to `/api/agent` (the chat /
   * activity proxy path). */
  apiBase?: string;
  fetcher?: typeof fetch;
  /** Poll cadence — defaults to 1000 ms. Tests pass a much smaller
   * value or 0 (disable). */
  pollIntervalMs?: number;
  className?: string;
}

const ORCHESTRATOR_LABEL = "orchestrator";

/** Tree node assembled from sessions + their direct child sessions. */
interface SessionNode {
  record: ActivitySessionRecord;
  children: SessionNode[];
  calls: ActivityCallRecord[];
}

export function buildSessionTree(
  sessions: ActivitySessionRecord[],
  calls: ActivityCallRecord[],
): SessionNode[] {
  const bySession = new Map<string, SessionNode>();
  for (const s of sessions) {
    bySession.set(s.session_id, { record: s, children: [], calls: [] });
  }
  for (const c of calls) {
    const owner = bySession.get(c.session_id);
    if (owner) owner.calls.push(c);
  }
  const roots: SessionNode[] = [];
  for (const node of bySession.values()) {
    const parent = node.record.parent_session_id
      ? bySession.get(node.record.parent_session_id)
      : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  // Stable ordering — `started_at` is ISO so a string sort works.
  const byStart = (a: SessionNode, b: SessionNode) =>
    a.record.started_at.localeCompare(b.record.started_at);
  roots.sort(byStart);
  for (const node of bySession.values()) {
    node.children.sort(byStart);
    node.calls.sort((a, b) => a.started_at.localeCompare(b.started_at));
  }
  return roots;
}

export function SubagentActivityPanel(props: SubagentActivityPanelProps) {
  const {
    apiBase = "/api/agent",
    fetcher,
    pollIntervalMs = 1000,
    className,
  } = props;
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const [sessions, setSessions] = useState<ActivitySessionRecord[]>([]);
  const [calls, setCalls] = useState<ActivityCallRecord[]>([]);
  const [highWater, setHighWater] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // useRef so the polling effect always reads the latest seq without
  // re-running on every snapshot tick.
  const highWaterRef = useRef(0);
  useEffect(() => {
    highWaterRef.current = highWater;
  }, [highWater]);

  const poll = useCallback(async () => {
    try {
      const url =
        highWaterRef.current === 0
          ? `${apiBase}/activity/snapshot`
          : `${apiBase}/activity/snapshot?since=${highWaterRef.current}`;
      const resp = await fetchImpl(url, { method: "GET" });
      const body = (await resp.json()) as SnapshotResponse;
      if (!resp.ok || body.ok === false) {
        throw new Error(body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`);
      }
      const newSessions = body.sessions ?? [];
      const newCalls = body.calls ?? [];
      if (newSessions.length > 0) {
        setSessions((prev) => mergeBySessionId(prev, newSessions));
      }
      if (newCalls.length > 0) {
        setCalls((prev) => mergeByToolUseId(prev, newCalls));
      }
      if (typeof body.high_water_seq === "number") {
        setHighWater(body.high_water_seq);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [apiBase, fetchImpl]);

  useEffect(() => {
    // Always fire once on mount, regardless of interval, so the
    // panel populates even when polling is disabled.
    void poll();
    if (pollIntervalMs <= 0) return;
    const handle = setInterval(() => {
      void poll();
    }, pollIntervalMs);
    return () => clearInterval(handle);
  }, [poll, pollIntervalMs]);

  const clear = useCallback(async () => {
    try {
      const resp = await fetchImpl(`${apiBase}/activity`, { method: "DELETE" });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as SnapshotResponse;
        throw new Error(body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`);
      }
      setSessions([]);
      setCalls([]);
      setHighWater(0);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase, fetchImpl]);

  const tree = useMemo(() => buildSessionTree(sessions, calls), [sessions, calls]);

  const totals = useMemo(() => {
    const active = sessions.filter((s) => !s.ended_at).length;
    const running = calls.filter((c) => c.status === "running").length;
    const errors = calls.filter((c) => c.status === "error").length;
    return { active, running, errors };
  }, [sessions, calls]);

  return (
    <div
      data-testid="subagent-activity-panel"
      data-status={loading ? "loading" : "ready"}
      className={className}
      style={panelStyle}
    >
      <header style={headerStyle}>
        <span style={{ flex: 1 }}>Subagent activity</span>
        <span
          data-testid="subagent-activity-summary"
          style={{ color: "#9ca3af", fontSize: 11 }}
        >
          {sessions.length} sessions · {calls.length} calls · {totals.active} active ·{" "}
          {totals.running} running · {totals.errors} errored
        </span>
        <button
          type="button"
          onClick={() => void clear()}
          style={clearButtonStyle}
          data-testid="subagent-activity-clear"
        >
          Clear
        </button>
      </header>

      {error ? (
        <div data-testid="subagent-activity-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}

      {tree.length === 0 ? (
        <p
          data-testid="subagent-activity-empty"
          style={{ padding: 12, color: "#9ca3af", fontSize: 12, margin: 0 }}
        >
          No sessions yet — start a chat and the orchestrator + any
          spawned subagents will show up here.
        </p>
      ) : (
        <div style={treeContainer}>
          {tree.map((node) => (
            <SessionCard key={node.record.session_id} node={node} depth={0} />
          ))}
        </div>
      )}
    </div>
  );
}

function SessionCard({ node, depth }: { node: SessionNode; depth: number }) {
  const { record, children, calls } = node;
  const label = record.agent_id || ORCHESTRATOR_LABEL;
  const live = !record.ended_at;
  return (
    <div
      data-testid="subagent-session"
      data-session-id={record.session_id}
      data-agent-id={label}
      data-parent-session-id={record.parent_session_id ?? ""}
      data-live={live ? "true" : "false"}
      data-depth={depth}
      style={{ ...sessionCardStyle, marginLeft: depth * 16 }}
    >
      <div style={sessionHeaderRow}>
        <span style={{ ...agentBadge, background: badgeColor(label, live) }}>{label}</span>
        <span style={{ color: "#cbd5e1", fontSize: 11 }}>
          {short(record.session_id)}
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ color: live ? "#34d399" : "#9ca3af", fontSize: 11 }}>
          {live ? "● running" : "● done"}
        </span>
      </div>
      {calls.length === 0 ? (
        <p style={{ color: "#9ca3af", fontSize: 11, margin: "4px 0 0" }}>
          no tool calls yet
        </p>
      ) : (
        <table style={callTableStyle}>
          <tbody>
            {calls.map((c) => (
              <tr
                key={c.tool_use_id}
                data-testid="subagent-call"
                data-tool-use-id={c.tool_use_id}
                data-status={c.status}
              >
                <td style={callTdStyle}>
                  <span style={{ ...statusDot, background: statusColor(c.status) }} />
                  {c.tool_name}
                </td>
                <td style={{ ...callTdStyle, color: "#9ca3af", textAlign: "right" }}>
                  {c.duration_ms != null ? `${c.duration_ms.toFixed(0)} ms` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {children.length > 0 ? (
        <div style={childrenStack}>
          {children.map((child) => (
            <SessionCard key={child.record.session_id} node={child} depth={depth + 1} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function badgeColor(agentId: string, live: boolean): string {
  if (!live) return "#27272a";
  switch (agentId) {
    case "decoupling-auditor":
      return "#7c2d12";
    case "bom-sourcer":
      return "#14532d";
    case "placement-explorer":
      return "#1e3a8a";
    case ORCHESTRATOR_LABEL:
      return "#3f3f46";
    default:
      return "#1f2937";
  }
}

function statusColor(s: ActivityCallRecord["status"]): string {
  switch (s) {
    case "running":
      return "#34d399";
    case "ok":
      return "#60a5fa";
    case "error":
      return "#ff7875";
  }
}

function short(id: string): string {
  if (id.length <= 8) return id;
  return `${id.slice(0, 4)}…${id.slice(-3)}`;
}

function mergeBySessionId(
  prev: ActivitySessionRecord[],
  incoming: ActivitySessionRecord[],
): ActivitySessionRecord[] {
  const byId = new Map(prev.map((s) => [s.session_id, s]));
  for (const s of incoming) byId.set(s.session_id, s);
  return Array.from(byId.values());
}

function mergeByToolUseId(
  prev: ActivityCallRecord[],
  incoming: ActivityCallRecord[],
): ActivityCallRecord[] {
  const byId = new Map(prev.map((c) => [c.tool_use_id, c]));
  for (const c of incoming) byId.set(c.tool_use_id, c);
  return Array.from(byId.values());
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

const treeContainer: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 12,
};

const sessionCardStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  background: "#0d1018",
  border: "1px solid #1a1f2a",
  borderRadius: 4,
  padding: 8,
};

const sessionHeaderRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const agentBadge: React.CSSProperties = {
  display: "inline-block",
  padding: "2px 8px",
  borderRadius: 999,
  fontSize: 10,
  color: "#f9fafb",
  letterSpacing: 0.3,
  textTransform: "uppercase",
};

const callTableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 11,
};

const callTdStyle: React.CSSProperties = {
  padding: "2px 4px",
  color: "#e2e8f0",
};

const statusDot: React.CSSProperties = {
  display: "inline-block",
  width: 8,
  height: 8,
  borderRadius: 999,
  marginRight: 6,
  verticalAlign: "middle",
};

const childrenStack: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  marginTop: 4,
};

const clearButtonStyle: React.CSSProperties = {
  padding: "3px 8px",
  background: "transparent",
  color: "#cbd5e1",
  border: "1px solid #3f3f46",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
};
