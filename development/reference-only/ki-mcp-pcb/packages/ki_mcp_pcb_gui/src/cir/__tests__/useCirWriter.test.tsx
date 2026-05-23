import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Board, CirState } from '../../api/client'
import { putCir, putCirBoard } from '../../api/client'
import { useCirWriter } from '../useCirWriter'

vi.mock('../../api/client', () => ({
  putCir: vi.fn(),
  putCirBoard: vi.fn(),
}))

const mockPutCir = vi.mocked(putCir)
const mockPutCirBoard = vi.mocked(putCirBoard)

const DEBOUNCE_MS = 800

function fakeState(text: string): CirState {
  return {
    exists: true,
    text,
    parse_error: null,
    board: null,
    validation: null,
    bom: [],
    sourcing: [],
  }
}

const A_BOARD: Board = {
  cir_version: '0.4',
  name: 'demo',
  components: [],
  nets: [],
  constraints: [],
}

beforeEach(() => {
  vi.useFakeTimers()
  mockPutCir.mockReset()
  mockPutCirBoard.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

/** Advance fake timers and let queued microtasks resolve. */
async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useCirWriter — text debounce', () => {
  it('writes once after the debounce, with the latest text', async () => {
    mockPutCir.mockResolvedValue(fakeState('final'))
    const onState = vi.fn()
    const { result } = renderHook(() => useCirWriter({ onState }))

    act(() => {
      result.current.enqueueText('first')
      result.current.enqueueText('second')
      result.current.enqueueText('final')
    })

    expect(mockPutCir).not.toHaveBeenCalled()
    await tick(DEBOUNCE_MS)
    expect(mockPutCir).toHaveBeenCalledExactlyOnceWith('final')
    expect(onState).toHaveBeenCalledWith(fakeState('final'))
  })

  it('flush() forces a pending debounced write to fire immediately', async () => {
    mockPutCir.mockResolvedValue(fakeState('pending'))
    const onState = vi.fn()
    const { result } = renderHook(() => useCirWriter({ onState }))

    act(() => {
      result.current.enqueueText('pending')
    })

    let flushPromise: Promise<void>
    act(() => {
      flushPromise = result.current.flush()
    })
    await act(async () => {
      await flushPromise
    })

    expect(mockPutCir).toHaveBeenCalledExactlyOnceWith('pending')
  })
})

describe('useCirWriter — writeBoard serialises after pending text', () => {
  it('flushes pending text before writing the board', async () => {
    const order: string[] = []
    mockPutCir.mockImplementation(async (text: string) => {
      order.push(`text:${text}`)
      return fakeState(text)
    })
    mockPutCirBoard.mockImplementation(async () => {
      order.push('board')
      return fakeState('board')
    })

    const { result } = renderHook(() => useCirWriter({ onState: () => {} }))

    let boardPromise: Promise<void>
    act(() => {
      result.current.enqueueText('typed-but-unsaved')
      boardPromise = result.current.writeBoard(A_BOARD)
    })
    await act(async () => {
      await boardPromise
    })

    // The board write only happens AFTER the typed text was flushed first.
    expect(order).toEqual(['text:typed-but-unsaved', 'board'])
  })

  it('serialises two writeBoard calls so they never run concurrently', async () => {
    let inflight = 0
    let peak = 0
    mockPutCirBoard.mockImplementation(async () => {
      inflight += 1
      peak = Math.max(peak, inflight)
      await new Promise((r) => setTimeout(r, 10))
      inflight -= 1
      return fakeState('done')
    })

    const { result } = renderHook(() => useCirWriter({ onState: () => {} }))

    let p1: Promise<void>
    let p2: Promise<void>
    act(() => {
      p1 = result.current.writeBoard(A_BOARD)
      p2 = result.current.writeBoard(A_BOARD)
    })
    await tick(40)
    await act(async () => {
      await p1
      await p2
    })

    expect(peak).toBe(1)
    expect(mockPutCirBoard).toHaveBeenCalledTimes(2)
  })
})

describe('useCirWriter — status + error reporting', () => {
  it('moves through saving → idle on a clean write', async () => {
    let resolveWrite: (state: CirState) => void = () => {}
    mockPutCir.mockImplementation(
      () => new Promise<CirState>((r) => (resolveWrite = r)),
    )
    const { result } = renderHook(() =>
      useCirWriter({ onState: () => {} }),
    )

    act(() => {
      result.current.enqueueText('x')
    })
    await tick(DEBOUNCE_MS)
    expect(result.current.status).toBe('saving')

    await act(async () => {
      resolveWrite(fakeState('x'))
    })
    expect(result.current.status).toBe('idle')
  })

  it('reports an error then recovers on the next successful write', async () => {
    mockPutCir
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(fakeState('ok'))

    const { result } = renderHook(() =>
      useCirWriter({ onState: () => {} }),
    )

    act(() => {
      result.current.enqueueText('bad')
    })
    await tick(DEBOUNCE_MS)
    expect(result.current.status).toBe('error')
    expect(result.current.error).toBe('boom')

    act(() => {
      result.current.enqueueText('ok')
    })
    await tick(DEBOUNCE_MS)
    expect(result.current.status).toBe('idle')
    expect(result.current.error).toBeNull()
  })
})
