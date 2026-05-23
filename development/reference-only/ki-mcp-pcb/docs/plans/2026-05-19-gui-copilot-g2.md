# Implementation Plan — GUI Co-pilot, Milestone G2

| | |
|---|---|
| **Date** | 2026-05-19 |
| **Implements** | [`SPEC-1`](../specs/SPEC-1-gui-copilot.md) — milestone **G2: Claude agent integration** |
| **Builds on** | G1 ([`2026-05-19-gui-copilot-g1.md`](./2026-05-19-gui-copilot-g1.md)) — complete |
| **Packages** | `ki_mcp_pcb_web` (backend), `ki_mcp_pcb_gui` (frontend) |

## Goal

A user can hold a streamed conversation with Claude inside the GUI, and Claude
can run the whole ki-mcp-pcb pipeline agentically and edit the working CIR —
with the irreversible actions (CIR writes, fab export, sign-off) gated behind
a GUI approve/reject. A user never needs a terminal.

## Architecture (resolved — SPEC-1 §6.5)

- The backend embeds the **Claude Agent SDK** (`claude-agent-sdk`, verified
  v0.2.82) as a new optional `agent` extra on `ki_mcp_pcb_web`.
- One `ClaudeSDKClient` session per WebSocket connection, configured with
  `ClaudeAgentOptions`: `system_prompt` (CIR contract + working dir),
  `mcp_servers` wiring in `ki_mcp_pcb_server` (the agent gets the same tools
  as terminal Claude Code), `cwd` = the GUI working directory (NFR-7 sandbox),
  and a `can_use_tool` callback that implements the approval gates.
- `WS /api/agent` bridges browser ↔ agent: user prompts in; `text`,
  `tool_use`, `tool_result`, `approval_request`, `cir_changed`, `error`
  events out.
- Tests stub the SDK transport — no live Anthropic calls in CI (SPEC-1 §8).

## Tasks

Each ships with its tests (a separate todo, per the G1 convention). The suite
stays green task-by-task: `pytest` + `ruff` + `mypy` + the TS `lint`/`test`/`build`.

### G2-T1 — Backend: `agent` extra + agent-session module

Add `claude-agent-sdk` as the `ki_mcp_pcb_web[agent]` optional extra. New
`agent.py`: build the `ClaudeAgentOptions` (system prompt, `mcp_servers` →
`ki_mcp_pcb_server`, `cwd` = working dir) and an `AgentSession` wrapper around
`ClaudeSDKClient` exposing `send(prompt)` → async event iterator. SDK import
is lazy so the pipeline-only GUI runs without it.

### G2-T2 — Backend: `WS /api/agent`

A WebSocket endpoint: receive user-message JSON; drive the `AgentSession`;
translate SDK messages (`AssistantMessage`/`ToolUseBlock`/`ToolResultBlock`/
`ResultMessage`) into the GUI event types and push them to the socket. When
the SDK or Anthropic credentials are absent, send a structured
`agent_unavailable` event rather than failing.

### G2-T3 — Backend: approval-gate `can_use_tool` callback

A `can_use_tool` callback that, for irreversible/outward-facing tools (CIR file
writes, `export_fab`, any `signoff.*` change), emits an `approval_request`
over the WebSocket and awaits the user's approve/reject before returning
`PermissionResultAllow`/`PermissionResultDeny`. Everything else is auto-allowed.
Enforced backend-side (SPEC-1 FR-16).

### G2-T4 — Frontend: WebSocket client + chat panel

`src/api/agent.ts` — a typed WebSocket client (connect, send prompt, event
stream, reconnect). `src/chat/ChatPanel.tsx` — the streamed conversation with
a prompt box; wired into the right pane of `App.tsx`.

### G2-T5 — Frontend: tool-call rendering + approval prompts

Render `tool_use`/`tool_result` events as inline conversation items, and
`approval_request` events as an approve/reject prompt that sends the decision
back over the socket.

### G2-T6 — Frontend: `cir_changed` refreshes the editor

When the agent edits the CIR, the backend emits `cir_changed`; the editor and
the validation/results panes reload from `GET /api/cir` (SPEC-1 FR-17).

### G2-T7 — Integration: optional extra, graceful degrade, CI

`uv run kimp serve` works with or without the `agent` extra; the chat panel
shows a clear "connect Claude" message when the SDK/auth is absent. CI: the
backend `agent` path is covered with a stubbed SDK transport.

## Verification gate

G2 is complete when the suite is green (`pytest`, `ruff`, `mypy`, the TS
`lint`/`test`/`build`), the chat panel streams a (stubbed-in-tests) agent
conversation, the approval gate is backend-enforced, and no pipeline logic
was added outside `ki_mcp_pcb_core`.

## Risks

| Risk | Mitigation |
|---|---|
| Agent SDK API drift across pre-1.0 releases | All SDK calls isolated in `agent.py`; pin `claude-agent-sdk`. |
| Live Anthropic calls would make CI costly/flaky | Stub the SDK transport in tests; never call the live API in CI. |
| WebSocket ↔ async-agent bridge complexity | Mirror the G1 SSE pattern — a queue between the agent loop and the socket generator. |
| Agentic tool access is powerful | `cwd` sandbox + the `can_use_tool` approval gate, both backend-enforced. |
| Anthropic auth absent on the user's machine | `agent_unavailable` event; the pipeline GUI keeps working. |

## Status — completed 2026-05-19

All seven tasks (G2-T1 … G2-T7) and their paired test todos are done. The
verification gate is green: backend `pytest` (379 passed, 2 skipped — the live
LLM evals), `ruff`, `mypy` (strict, 61 files); frontend `npm run lint` /
`test` (42 passed) / `build`.

Notable resolution beyond the original plan: the FR-16 approval gate also
covers `Bash` commands that name the working CIR file — `agent.is_cir_write`
flags `sed -i` / `> board.cir.yaml` so a shell write cannot slip past the
file-write-tool gate. Next milestone: **G3** (working-directory management,
new-project flow).
