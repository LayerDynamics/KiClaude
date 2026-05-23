import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AgentClient,
  AgentClientHandlers,
  AgentEvent,
} from '../../api/agent'
import { connectAgent } from '../../api/agent'
import { ChatPanel } from '../ChatPanel'

vi.mock('../../api/agent', () => ({
  connectAgent: vi.fn(),
}))

const mockConnect = vi.mocked(connectAgent)

/** A captured connection — the handlers ChatPanel wired + a stub client. */
interface Harness {
  handlers: AgentClientHandlers
  client: {
    sendPrompt: ReturnType<typeof vi.fn>
    sendApproval: ReturnType<typeof vi.fn>
    close: ReturnType<typeof vi.fn>
  }
  unmount: () => void
}

/** Render ChatPanel and capture the agent connection it opened. */
function renderChat(props: { onCirChanged?: () => void } = {}): Harness {
  let handlers: AgentClientHandlers | undefined
  const client = {
    sendPrompt: vi.fn(),
    sendApproval: vi.fn(),
    close: vi.fn(),
  }
  mockConnect.mockImplementation((given) => {
    handlers = given
    return client as AgentClient
  })
  const { unmount } = render(<ChatPanel onCirChanged={props.onCirChanged} />)
  if (!handlers) throw new Error('connectAgent was not called')
  return { handlers, client, unmount }
}

/** Type into the composer and press the Send button. */
function sendPrompt(text: string): void {
  const input = screen.getByPlaceholderText('Message Claude…')
  fireEvent.change(input, { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
}

function emit(handlers: AgentClientHandlers, event: AgentEvent): void {
  act(() => handlers.onEvent(event))
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ChatPanel', () => {
  it('connects on mount and shows Ready once the socket opens', () => {
    const { handlers } = renderChat()
    expect(mockConnect).toHaveBeenCalledOnce()
    expect(screen.getByText('Connecting to Claude…')).toBeInTheDocument()

    act(() => handlers.onOpen?.())
    expect(screen.getByText('Ready')).toBeInTheDocument()
  })

  it('sends a prompt, echoes the user message, and shows working status', () => {
    const { handlers, client } = renderChat()
    act(() => handlers.onOpen?.())

    sendPrompt('validate my board')

    expect(client.sendPrompt).toHaveBeenCalledWith('validate my board')
    expect(screen.getByText('validate my board')).toBeInTheDocument()
    expect(screen.getByText('Claude is working…')).toBeInTheDocument()
  })

  it('renders streamed agent text and returns to Ready on done', () => {
    const { handlers } = renderChat()
    act(() => handlers.onOpen?.())
    sendPrompt('hi')

    emit(handlers, { type: 'text', text: 'The board validates cleanly.' })
    expect(
      screen.getByText('The board validates cleanly.'),
    ).toBeInTheDocument()

    emit(handlers, { type: 'done', is_error: false, result: 'ok', cost_usd: 0.01 })
    expect(screen.getByText('Ready')).toBeInTheDocument()
  })

  it('shows an error notice when the agent reports an error', () => {
    const { handlers } = renderChat()
    act(() => handlers.onOpen?.())
    emit(handlers, { type: 'error', detail: 'agent transport broke' })
    expect(screen.getByText('agent transport broke')).toBeInTheDocument()
  })

  it('disables the composer when the co-pilot is unavailable', () => {
    const { handlers } = renderChat()
    emit(handlers, {
      type: 'agent_unavailable',
      detail: 'the Claude Agent SDK is not installed',
    })
    expect(screen.getByText('Co-pilot unavailable')).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText('Connect Claude to use the co-pilot.'),
    ).toBeDisabled()
  })

  it('sends on Enter but inserts a newline on Shift+Enter', () => {
    const { handlers, client } = renderChat()
    act(() => handlers.onOpen?.())
    const input = screen.getByPlaceholderText('Message Claude…')

    fireEvent.change(input, { target: { value: 'first turn' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(client.sendPrompt).not.toHaveBeenCalled()

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(client.sendPrompt).toHaveBeenCalledWith('first turn')
  })

  it('closes the socket on unmount', () => {
    const { client, unmount } = renderChat()
    unmount()
    expect(client.close).toHaveBeenCalled()
  })
})

describe('ChatPanel — tool calls (G2-T5)', () => {
  it('renders a tool_use as an inline running tool item', () => {
    const { handlers } = renderChat()
    act(() => handlers.onOpen?.())
    emit(handlers, {
      type: 'tool_use',
      id: 'tu-1',
      name: 'tool_validate_cir',
      input: { source_path: 'board.cir.yaml' },
    })
    expect(screen.getByText('tool_validate_cir')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText(/board\.cir\.yaml/)).toBeInTheDocument()
  })

  it('resolves the matching tool item when its result arrives', () => {
    const { handlers } = renderChat()
    act(() => handlers.onOpen?.())
    emit(handlers, {
      type: 'tool_use',
      id: 'tu-1',
      name: 'tool_drc',
      input: {},
    })
    emit(handlers, {
      type: 'tool_result',
      tool_use_id: 'tu-1',
      content: 'no DRC violations',
      is_error: false,
    })
    expect(screen.getByText('ok')).toBeInTheDocument()
    expect(screen.getByText('no DRC violations')).toBeInTheDocument()
    expect(screen.queryByText('running')).not.toBeInTheDocument()
  })

  it('marks a tool item as errored on a failed result', () => {
    const { handlers } = renderChat()
    act(() => handlers.onOpen?.())
    emit(handlers, { type: 'tool_use', id: 'tu-9', name: 'tool_build', input: {} })
    emit(handlers, {
      type: 'tool_result',
      tool_use_id: 'tu-9',
      content: 'build failed',
      is_error: true,
    })
    expect(screen.getByText('error')).toBeInTheDocument()
    expect(screen.getByText('build failed')).toBeInTheDocument()
  })
})

describe('ChatPanel — approval prompts (G2-T5)', () => {
  function emitApproval(handlers: AgentClientHandlers): void {
    emit(handlers, {
      type: 'approval_request',
      id: 'req-1',
      tool: 'tool_export_fab',
      input: { target: 'jlcpcb' },
      reason: 'exports a manufacturing (fab) package',
    })
  }

  it('renders an approval_request with approve/reject buttons', () => {
    const { handlers } = renderChat()
    act(() => handlers.onOpen?.())
    emitApproval(handlers)

    expect(screen.getByText('Approval needed')).toBeInTheDocument()
    expect(screen.getByText('tool_export_fab')).toBeInTheDocument()
    expect(screen.getByText(/exports a manufacturing/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('sends an allow decision and shows Approved when Approve is clicked', () => {
    const { handlers, client } = renderChat()
    act(() => handlers.onOpen?.())
    emitApproval(handlers)

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    expect(client.sendApproval).toHaveBeenCalledWith('req-1', 'allow')
    expect(screen.getByText('Approved')).toBeInTheDocument()
    // The buttons are gone once the decision is made.
    expect(
      screen.queryByRole('button', { name: 'Approve' }),
    ).not.toBeInTheDocument()
  })

  it('sends a deny decision and shows Rejected when Reject is clicked', () => {
    const { handlers, client } = renderChat()
    act(() => handlers.onOpen?.())
    emitApproval(handlers)

    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))

    expect(client.sendApproval).toHaveBeenCalledWith('req-1', 'deny')
    expect(screen.getByText('Rejected')).toBeInTheDocument()
  })
})

describe('ChatPanel — cir_changed refresh (G2-T6)', () => {
  it('calls onCirChanged and shows a notice when the CIR is updated', () => {
    const onCirChanged = vi.fn()
    const { handlers } = renderChat({ onCirChanged })
    act(() => handlers.onOpen?.())

    emit(handlers, { type: 'cir_changed' })

    expect(onCirChanged).toHaveBeenCalledOnce()
    expect(screen.getByText(/Claude updated the working CIR/)).toBeInTheDocument()
  })
})

describe('ChatPanel — full conversation smoke (G2-T7)', () => {
  it('renders a complete prompt → approve → CIR-change → done turn', () => {
    const onCirChanged = vi.fn()
    const { handlers, client } = renderChat({ onCirChanged })
    act(() => handlers.onOpen?.())

    // The user asks for a board change.
    sendPrompt('add a 100nF cap on the 3V3 rail')
    expect(client.sendPrompt).toHaveBeenCalledWith(
      'add a 100nF cap on the 3V3 rail',
    )
    expect(screen.getByText('Claude is working…')).toBeInTheDocument()

    // Claude explains, then asks to write the CIR — gated for approval.
    emit(handlers, { type: 'text', text: 'Adding the decoupling cap.' })
    emit(handlers, {
      type: 'tool_use',
      id: 'tu-w',
      name: 'Write',
      input: { file_path: 'board.cir.yaml' },
    })
    emit(handlers, {
      type: 'approval_request',
      id: 'req-1',
      tool: 'Write',
      input: { file_path: 'board.cir.yaml' },
      reason: 'writes the working CIR file (board.cir.yaml)',
    })
    expect(screen.getByText('Approval needed')).toBeInTheDocument()

    // The user approves; the write runs and the CIR refreshes.
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(client.sendApproval).toHaveBeenCalledWith('req-1', 'allow')

    emit(handlers, {
      type: 'tool_result',
      tool_use_id: 'tu-w',
      content: 'written',
      is_error: false,
    })
    emit(handlers, { type: 'cir_changed' })
    emit(handlers, { type: 'done', is_error: false, result: 'ok', cost_usd: 0.02 })

    // The whole turn is on screen and the editor was told to reload.
    expect(screen.getByText('Adding the decoupling cap.')).toBeInTheDocument()
    // 'Write' shows twice — the tool item and the (now-resolved) approval.
    expect(screen.getAllByText('Write')).toHaveLength(2)
    expect(screen.getByText('Approved')).toBeInTheDocument()
    expect(onCirChanged).toHaveBeenCalledOnce()
    expect(screen.getByText('Ready')).toBeInTheDocument()
  })
})
