// Typed WebSocket client for the Claude co-pilot chat (`WS /api/agent`).
//
// The wire protocol mirrors the backend in ki_mcp_pcb_web/server.py: the
// client sends `prompt` / `approval` messages; the server streams the
// agent's `text`/`tool_use`/`tool_result`/`done` events plus
// `approval_request`, `cir_changed` and `agent_unavailable`/`error`.

/** One event streamed from the agent to the GUI. */
export type AgentEvent =
  | { type: 'text'; text: string }
  | { type: 'thinking'; text: string }
  | {
      type: 'tool_use'
      id: string
      name: string
      input: Record<string, unknown>
    }
  | {
      type: 'tool_result'
      tool_use_id: string
      content: unknown
      is_error: boolean
    }
  | {
      type: 'done'
      is_error: boolean
      result: string | null
      cost_usd: number | null
    }
  | {
      type: 'approval_request'
      id: string
      tool: string
      input: Record<string, unknown>
      reason: string
    }
  | { type: 'cir_changed' }
  | { type: 'agent_unavailable'; detail: string }
  | { type: 'error'; detail: string }

/** A user's approve/reject decision on an `approval_request`. */
export type ApprovalDecision = 'allow' | 'deny'

export interface AgentClientHandlers {
  /** Each agent event, in arrival order. */
  onEvent: (event: AgentEvent) => void
  /** The socket opened and is ready for prompts. */
  onOpen?: () => void
  /** The socket closed (cleanly or not) — no more events will arrive. */
  onClose?: () => void
}

/** A live co-pilot connection. */
export interface AgentClient {
  /** Send one user turn. */
  sendPrompt: (text: string) => void
  /** Answer a pending `approval_request` by its id. */
  sendApproval: (id: string, decision: ApprovalDecision) => void
  /** Close the connection. */
  close: () => void
}

/** Build the absolute ws(s):// URL for the agent endpoint (same origin). */
export function agentSocketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/agent`
}

/**
 * Open a co-pilot WebSocket and stream its events to `handlers`. The
 * returned client sends prompts/approvals and closes the socket. Call
 * `connectAgent` again to reconnect after a close.
 */
export function connectAgent(handlers: AgentClientHandlers): AgentClient {
  const socket = new WebSocket(agentSocketUrl())

  socket.addEventListener('open', () => handlers.onOpen?.())
  socket.addEventListener('close', () => handlers.onClose?.())
  socket.addEventListener('message', (event) => {
    let parsed: AgentEvent
    try {
      parsed = JSON.parse(event.data as string) as AgentEvent
    } catch {
      handlers.onEvent({ type: 'error', detail: 'malformed event from agent' })
      return
    }
    handlers.onEvent(parsed)
  })
  socket.addEventListener('error', () => {
    // A transport error always precedes a `close`; surface it once so the
    // panel can show a reason rather than a silent disconnect.
    handlers.onEvent({
      type: 'error',
      detail: 'connection to the co-pilot was lost',
    })
  })

  const send = (payload: unknown): void => {
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload))
    }
  }

  return {
    sendPrompt: (text) => send({ type: 'prompt', text }),
    sendApproval: (id, decision) => send({ type: 'approval', id, decision }),
    close: () => socket.close(),
  }
}
