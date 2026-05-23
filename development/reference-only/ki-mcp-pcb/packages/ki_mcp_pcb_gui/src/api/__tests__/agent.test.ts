import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentEvent } from '../agent'
import { agentSocketUrl, connectAgent } from '../agent'

/** A minimal stand-in for the browser WebSocket, drivable from tests. */
class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 3
  static last: FakeWebSocket | null = null

  readonly url: string
  readyState = FakeWebSocket.CONNECTING
  sent: string[] = []
  closed = false
  private listeners: Record<string, ((e: unknown) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeWebSocket.last = this
  }

  addEventListener(type: string, fn: (e: unknown) => void): void {
    ;(this.listeners[type] ??= []).push(fn)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.closed = true
    this.readyState = FakeWebSocket.CLOSED
    this.fire('close', {})
  }

  /** Test helper — dispatch a registered listener. */
  fire(type: string, event: unknown): void {
    for (const fn of this.listeners[type] ?? []) fn(event)
  }

  /** Test helper — move to OPEN and fire `open`. */
  open(): void {
    this.readyState = FakeWebSocket.OPEN
    this.fire('open', {})
  }
}

beforeEach(() => {
  FakeWebSocket.last = null
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('agentSocketUrl', () => {
  it('builds a same-origin ws:// URL for the agent endpoint', () => {
    expect(agentSocketUrl()).toBe(`ws://${window.location.host}/api/agent`)
  })
})

describe('connectAgent', () => {
  it('parses streamed events and forwards them to onEvent', () => {
    const events: AgentEvent[] = []
    connectAgent({ onEvent: (e) => events.push(e) })
    const ws = FakeWebSocket.last!

    ws.fire('message', { data: JSON.stringify({ type: 'text', text: 'hi' }) })
    expect(events).toEqual([{ type: 'text', text: 'hi' }])
  })

  it('reports a malformed event instead of throwing', () => {
    const events: AgentEvent[] = []
    connectAgent({ onEvent: (e) => events.push(e) })
    FakeWebSocket.last!.fire('message', { data: 'not json' })
    expect(events[0]).toEqual({
      type: 'error',
      detail: 'malformed event from agent',
    })
  })

  it('relays open and close to their handlers', () => {
    const onOpen = vi.fn()
    const onClose = vi.fn()
    connectAgent({ onEvent: vi.fn(), onOpen, onClose })
    const ws = FakeWebSocket.last!

    ws.open()
    expect(onOpen).toHaveBeenCalledOnce()
    ws.close()
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('sends prompt and approval messages once the socket is open', () => {
    const client = connectAgent({ onEvent: vi.fn() })
    const ws = FakeWebSocket.last!
    ws.open()

    client.sendPrompt('validate the board')
    client.sendApproval('req-1', 'allow')

    expect(ws.sent.map((s) => JSON.parse(s))).toEqual([
      { type: 'prompt', text: 'validate the board' },
      { type: 'approval', id: 'req-1', decision: 'allow' },
    ])
  })

  it('drops sends while the socket is not open', () => {
    const client = connectAgent({ onEvent: vi.fn() })
    // Never opened — still CONNECTING.
    client.sendPrompt('too early')
    expect(FakeWebSocket.last!.sent).toEqual([])
  })

  it('emits an error event on a transport error', () => {
    const events: AgentEvent[] = []
    connectAgent({ onEvent: (e) => events.push(e) })
    FakeWebSocket.last!.fire('error', {})
    expect(events[0]).toEqual({
      type: 'error',
      detail: 'connection to the co-pilot was lost',
    })
  })
})
