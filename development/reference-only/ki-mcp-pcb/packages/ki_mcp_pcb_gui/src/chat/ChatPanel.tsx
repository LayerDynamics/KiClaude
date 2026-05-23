import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type AgentClient,
  type AgentEvent,
  type ApprovalDecision,
  connectAgent,
} from '../api/agent'

/** A tool call's lifecycle, mirrored from the agent's tool_use/tool_result. */
type ToolStatus = 'running' | 'ok' | 'error'

/** An approval request's state — pending until the user decides. */
type ApprovalState = 'pending' | ApprovalDecision

/** One conversation line, before it is assigned a render id. */
type ChatItemDraft =
  | { kind: 'user'; text: string }
  | { kind: 'text'; text: string }
  | { kind: 'thinking'; text: string }
  | { kind: 'notice'; tone: 'info' | 'error'; text: string }
  | {
      kind: 'tool'
      toolUseId: string
      name: string
      input: Record<string, unknown>
      status: ToolStatus
      result: string | null
    }
  | {
      kind: 'approval'
      requestId: string
      tool: string
      input: Record<string, unknown>
      reason: string
      state: ApprovalState
    }

/** A rendered conversation line — a draft plus its stable list key. */
type ChatItem = ChatItemDraft & { id: number }

/** Connection lifecycle, surfaced to the user as a status line. */
type ChatStatus = 'connecting' | 'ready' | 'thinking' | 'closed' | 'unavailable'

const STATUS_LABEL: Record<ChatStatus, string> = {
  connecting: 'Connecting to Claude…',
  ready: 'Ready',
  thinking: 'Claude is working…',
  closed: 'Disconnected',
  unavailable: 'Co-pilot unavailable',
}

/** Render a tool result, which may arrive as a string or a structured value. */
function describeContent(content: unknown): string {
  if (content == null) return ''
  if (typeof content === 'string') return content
  return JSON.stringify(content)
}

/** A short one-line summary of a tool call's arguments. */
function describeInput(input: Record<string, unknown>): string {
  const keys = Object.keys(input)
  if (keys.length === 0) return ''
  return JSON.stringify(input)
}

interface ChatLineProps {
  item: ChatItem
  /** Answer an approval request — only used by `approval` items. */
  onDecide: (requestId: string, decision: ApprovalDecision) => void
}

/** Render one conversation line — dispatches on the item kind. */
function ChatLine({ item, onDecide }: ChatLineProps) {
  if (item.kind === 'tool') {
    return (
      <div className={`chat__item chat__item--tool chat__tool--${item.status}`}>
        <span className="chat__role">Tool</span>
        <div className="chat__tool-head">
          <code className="chat__tool-name">{item.name}</code>
          <span className="chat__tool-status">{item.status}</span>
        </div>
        {describeInput(item.input) && (
          <pre className="chat__tool-io">{describeInput(item.input)}</pre>
        )}
        {item.result != null && item.result !== '' && (
          <pre className="chat__tool-io">{item.result}</pre>
        )}
      </div>
    )
  }

  if (item.kind === 'approval') {
    return (
      <div
        className={`chat__item chat__item--approval chat__approval--${item.state}`}
      >
        <span className="chat__role">Approval needed</span>
        <div className="chat__approval-body">
          Claude wants to run <code>{item.tool}</code>, which {item.reason}.
        </div>
        {describeInput(item.input) && (
          <pre className="chat__tool-io">{describeInput(item.input)}</pre>
        )}
        {item.state === 'pending' ? (
          <div className="chat__approval-actions">
            <button
              type="button"
              className="chat__approve"
              onClick={() => onDecide(item.requestId, 'allow')}
            >
              Approve
            </button>
            <button
              type="button"
              className="chat__reject"
              onClick={() => onDecide(item.requestId, 'deny')}
            >
              Reject
            </button>
          </div>
        ) : (
          <div className="chat__approval-outcome">
            {item.state === 'allow' ? 'Approved' : 'Rejected'}
          </div>
        )}
      </div>
    )
  }

  const role =
    item.kind === 'user'
      ? 'You'
      : item.kind === 'text'
        ? 'Claude'
        : item.kind === 'thinking'
          ? 'Thinking'
          : null

  return (
    <div className={`chat__item chat__item--${item.kind}`}>
      {role && <span className="chat__role">{role}</span>}
      <div
        className={
          item.kind === 'notice'
            ? `chat__notice chat__notice--${item.tone}`
            : 'chat__text'
        }
      >
        {item.text}
      </div>
    </div>
  )
}

interface ChatPanelProps {
  /**
   * Called when the co-pilot edits the working CIR (`cir_changed`), so the
   * editor and results panes can reload from disk (SPEC-1 FR-17).
   */
  onCirChanged?: () => void
}

/**
 * The Claude co-pilot chat (SPEC-1 §6.5): a streamed conversation with a
 * prompt box. One WebSocket per mount. Tool calls render inline; an
 * irreversible action surfaces an approve/reject prompt whose decision is
 * sent back over the socket (the backend gate blocks until then).
 */
export function ChatPanel({ onCirChanged }: ChatPanelProps = {}) {
  const [items, setItems] = useState<ChatItem[]>([])
  const [status, setStatus] = useState<ChatStatus>('connecting')
  const [draft, setDraft] = useState('')
  const clientRef = useRef<AgentClient | null>(null)
  const nextId = useRef(0)
  const logRef = useRef<HTMLDivElement | null>(null)

  // Read `onCirChanged` through a ref so it never re-creates `handleEvent`
  // (which would reconnect the WebSocket).
  const onCirChangedRef = useRef(onCirChanged)
  useEffect(() => {
    onCirChangedRef.current = onCirChanged
  }, [onCirChanged])

  const append = useCallback((item: ChatItemDraft) => {
    setItems((prev) => [...prev, { ...item, id: nextId.current++ }])
  }, [])

  const handleEvent = useCallback(
    (event: AgentEvent) => {
      switch (event.type) {
        case 'text':
          append({ kind: 'text', text: event.text })
          break
        case 'thinking':
          append({ kind: 'thinking', text: event.text })
          break
        case 'tool_use':
          append({
            kind: 'tool',
            toolUseId: event.id,
            name: event.name,
            input: event.input,
            status: 'running',
            result: null,
          })
          break
        case 'tool_result':
          // Resolve the matching tool item from its tool_use id.
          setItems((prev) =>
            prev.map((it) =>
              it.kind === 'tool' && it.toolUseId === event.tool_use_id
                ? {
                    ...it,
                    status: event.is_error ? 'error' : 'ok',
                    result: describeContent(event.content),
                  }
                : it,
            ),
          )
          break
        case 'approval_request':
          append({
            kind: 'approval',
            requestId: event.id,
            tool: event.tool,
            input: event.input,
            reason: event.reason,
            state: 'pending',
          })
          break
        case 'done':
          setStatus('ready')
          break
        case 'error':
          append({ kind: 'notice', tone: 'error', text: event.detail })
          setStatus('ready')
          break
        case 'agent_unavailable':
          append({ kind: 'notice', tone: 'error', text: event.detail })
          setStatus('unavailable')
          break
        case 'cir_changed':
          append({
            kind: 'notice',
            tone: 'info',
            text: 'Claude updated the working CIR — the editor reloaded.',
          })
          onCirChangedRef.current?.()
          break
        default:
          break
      }
    },
    [append],
  )

  // Open one connection per mount; close it on unmount. `handleEvent` is
  // stable, so this runs exactly once — the initial 'connecting' status set
  // by useState already holds until onOpen/onClose fires.
  useEffect(() => {
    const client = connectAgent({
      onEvent: handleEvent,
      onOpen: () => setStatus('ready'),
      onClose: () => {
        setStatus((prev) => (prev === 'unavailable' ? prev : 'closed'))
      },
    })
    clientRef.current = client
    return () => client.close()
  }, [handleEvent])

  // Keep the newest message in view (setting scrollTop, not scrollTo(),
  // so it also works under jsdom in tests).
  useEffect(() => {
    const log = logRef.current
    if (log) log.scrollTop = log.scrollHeight
  }, [items])

  const decideApproval = useCallback(
    (requestId: string, decision: ApprovalDecision) => {
      clientRef.current?.sendApproval(requestId, decision)
      setItems((prev) =>
        prev.map((it) =>
          it.kind === 'approval' && it.requestId === requestId
            ? { ...it, state: decision }
            : it,
        ),
      )
    },
    [],
  )

  const sendPrompt = useCallback(() => {
    const text = draft.trim()
    if (!text || status === 'connecting' || status === 'unavailable') return
    clientRef.current?.sendPrompt(text)
    append({ kind: 'user', text })
    setDraft('')
    setStatus('thinking')
  }, [append, draft, status])

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter sends; Shift+Enter inserts a newline.
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault()
        sendPrompt()
      }
    },
    [sendPrompt],
  )

  const canSend =
    draft.trim() !== '' && status !== 'connecting' && status !== 'unavailable'

  return (
    <div className="chat">
      <div className="chat__statusline" role="status">
        {STATUS_LABEL[status]}
      </div>

      <div className="chat__log" ref={logRef}>
        {items.length === 0 && (
          <p className="pane__placeholder">
            Ask Claude to validate, build, or change the board.
          </p>
        )}
        {items.map((item) => (
          <ChatLine key={item.id} item={item} onDecide={decideApproval} />
        ))}
      </div>

      <div className="chat__composer">
        <textarea
          className="chat__input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            status === 'unavailable'
              ? 'Connect Claude to use the co-pilot.'
              : 'Message Claude…'
          }
          rows={3}
          disabled={status === 'unavailable'}
        />
        <button
          type="button"
          className="chat__send"
          onClick={sendPrompt}
          disabled={!canSend}
        >
          Send
        </button>
      </div>
    </div>
  )
}
