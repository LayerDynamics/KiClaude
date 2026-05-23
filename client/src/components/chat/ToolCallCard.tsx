import { useState } from "react";

import { Card } from "../UI";

export interface ToolCallRecord {
  /** Stable identifier for the tool call (SDK's tool_use_id). */
  id: string;
  /** Tool name (e.g. `kc_symbol_add`, `mcp__kiclaude__kc_validate`). */
  tool_name: string;
  /** Args dict the agent sent. */
  input: Record<string, unknown>;
  /** Response body — `null` while the call is still in flight. */
  output: Record<string, unknown> | null;
  /** True while pending. */
  in_flight: boolean;
  /** Optional duration_ms from the PostToolUse hook. */
  duration_ms?: number | null;
  /** True if the call returned `ok=false` / `isError=true`. */
  error?: boolean;
}

export interface ToolCallCardProps {
  call: ToolCallRecord;
}

const DOT_COLOR: Record<string, string> = {
  running: "bg-amber-400",
  error: "bg-red-400",
  ok: "bg-emerald-400",
};

/**
 * M1-T-07 sub-component: collapsible card representing one MCP tool
 * invocation. Closed by default; expanding shows the input / output
 * JSON side-by-side. The status pill mirrors the lifecycle:
 * `running → ok | error`.
 */
export function ToolCallCard({ call }: ToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const status = call.in_flight ? "running" : call.error ? "error" : "ok";
  const tone = status === "error" ? "danger" : "muted";

  return (
    <Card
      tone={tone}
      flush
      data-testid={`tool-call-card-${call.id}`}
      data-status={status}
      className="my-1"
    >
      <button
        type="button"
        data-testid={`tool-call-toggle-${call.id}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm text-[var(--text-h)] hover:bg-[var(--code-bg)]"
      >
        <span
          aria-hidden="true"
          className={`inline-block h-2 w-2 rounded-full ${DOT_COLOR[status]}`}
        />
        <span className="font-semibold">{call.tool_name}</span>
        <span className="ml-auto text-[11px] text-[var(--text)]/60">
          {call.in_flight
            ? "…"
            : typeof call.duration_ms === "number"
              ? `${Math.round(call.duration_ms)}ms`
              : ""}
        </span>
        <span className="ml-1 text-[10px] text-[var(--text)]/70">
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open ? (
        <div
          data-testid={`tool-call-body-${call.id}`}
          className="border-t border-[var(--border)] px-2 py-1.5"
        >
          <h4 className="m-0 mb-0.5 mt-1.5 text-[10px] uppercase tracking-wide text-[var(--text)]/70">
            Input
          </h4>
          <pre className="m-0 max-h-60 overflow-auto rounded border border-[var(--border)] bg-[var(--code-bg)] p-1.5 font-mono text-[11px] whitespace-pre-wrap break-words text-[var(--text-h)]">
            {prettyJson(call.input)}
          </pre>
          <h4 className="m-0 mb-0.5 mt-1.5 text-[10px] uppercase tracking-wide text-[var(--text)]/70">
            Output
          </h4>
          {call.output === null && call.in_flight ? (
            <pre className="m-0 max-h-60 overflow-auto rounded border border-[var(--border)] bg-[var(--code-bg)] p-1.5 font-mono text-[11px] text-[var(--text)]/60">
              (running…)
            </pre>
          ) : (
            <pre className="m-0 max-h-60 overflow-auto rounded border border-[var(--border)] bg-[var(--code-bg)] p-1.5 font-mono text-[11px] whitespace-pre-wrap break-words text-[var(--text-h)]">
              {prettyJson(call.output ?? {})}
            </pre>
          )}
        </div>
      ) : null}
    </Card>
  );
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
