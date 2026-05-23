import { fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Board, CirState } from '../../api/client'
import { putCir, putCirBoard } from '../../api/client'
import { CirEditor } from '../../cir/CirEditor'
import { getCir } from '../../api/client'
import type { CirWriter } from '../../cir/useCirWriter'
import { useCirWriter } from '../../cir/useCirWriter'
import { BoardForm } from '../BoardForm'

vi.mock('../../api/client', () => ({
  putCir: vi.fn(),
  putCirBoard: vi.fn(),
  getCir: vi.fn(),
}))

const mockPutCir = vi.mocked(putCir)
const mockPutCirBoard = vi.mocked(putCirBoard)
const mockGetCir = vi.mocked(getCir)

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

function makeBoard(): Board {
  return {
    cir_version: '0.4',
    name: 'demo',
    description: null,
    components: [
      {
        refdes: 'U1',
        mpn: 'ATSAMD21G18A-AU',
        value: null,
        partition: 'digital',
        decoupling_pins: [],
        bga_pitch_mm: null,
        is_bridge: false,
      },
    ],
    nets: [],
    constraints: [],
  }
}

function stubWriter(overrides: Partial<CirWriter> = {}): CirWriter {
  return {
    status: 'idle',
    error: null,
    enqueueText: vi.fn(),
    flush: vi.fn().mockResolvedValue(undefined),
    writeBoard: vi.fn().mockResolvedValue(undefined),
    writeSignoff: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

beforeEach(() => {
  mockPutCir.mockReset()
  mockPutCirBoard.mockReset()
  mockGetCir.mockReset()
})

describe('BoardForm', () => {
  it('renders the four sub-forms with the initial board content', () => {
    render(<BoardForm board={makeBoard()} writer={stubWriter()} />)
    expect(screen.getByLabelText('refdes-0')).toHaveValue('U1')
    // Form sections present
    expect(screen.getByRole('region', { name: 'Components' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Nets' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Stackup' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Fab target' })).toBeInTheDocument()
  })

  it('passes the current draft to writer.writeBoard on Save', async () => {
    const writer = stubWriter()
    const onCirChanged = vi.fn()
    render(
      <BoardForm
        board={makeBoard()}
        writer={writer}
        onCirChanged={onCirChanged}
      />,
    )

    fireEvent.change(screen.getByLabelText('refdes-0'), {
      target: { value: 'U42' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save board' }))

    await waitFor(() => expect(writer.writeBoard).toHaveBeenCalledOnce())
    const submitted = vi.mocked(writer.writeBoard).mock.calls[0][0]
    expect(submitted.components?.[0].refdes).toBe('U42')
    await waitFor(() => expect(onCirChanged).toHaveBeenCalledOnce())
  })

  it('disables Save while the writer is saving', () => {
    render(
      <BoardForm board={makeBoard()} writer={stubWriter({ status: 'saving' })} />,
    )
    expect(screen.getByRole('button', { name: 'Save board' })).toBeDisabled()
    expect(screen.getByText('Saving…')).toBeInTheDocument()
  })

  it('surfaces the writer error in the status line', () => {
    render(
      <BoardForm
        board={makeBoard()}
        writer={stubWriter({ status: 'error', error: 'backend rejected' })}
      />,
    )
    expect(screen.getByText(/backend rejected/)).toBeInTheDocument()
  })
})

describe('BoardForm + CirEditor integration (single-flight)', () => {
  it('queues a pending text autosave BEFORE the form save runs', async () => {
    // Real useCirWriter — exercises the single-flight queue end-to-end.
    mockGetCir.mockResolvedValue(fakeState('name: demo\n'))
    const order: string[] = []
    mockPutCir.mockImplementation(async (text: string) => {
      order.push(`text:${text}`)
      return fakeState(text)
    })
    mockPutCirBoard.mockImplementation(async () => {
      order.push('board')
      return fakeState('# canonical')
    })

    const { result } = renderHook(() => useCirWriter({ onState: () => {} }))
    const writer = result.current

    // The text editor enqueues a debounced autosave...
    render(<CirEditor writer={writer} />)
    const textarea = await screen.findByLabelText('CIR YAML editor')
    fireEvent.change(textarea, { target: { value: 'name: pending\n' } })

    // ...and the form save fires before the debounce elapses. It must
    // flush the pending text first, then write the board — the user's
    // typing is never silently discarded.
    render(<BoardForm board={makeBoard()} writer={writer} />)
    fireEvent.click(screen.getByRole('button', { name: 'Save board' }))

    await waitFor(() => expect(mockPutCirBoard).toHaveBeenCalled())
    expect(order).toEqual(['text:name: pending\n', 'board'])
  })
})
