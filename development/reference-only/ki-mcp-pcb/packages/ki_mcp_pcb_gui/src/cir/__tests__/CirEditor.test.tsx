import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CirState } from '../../api/client'
import { getCir } from '../../api/client'
import { CirEditor } from '../CirEditor'
import type { CirWriter } from '../useCirWriter'

vi.mock('../../api/client', () => ({
  getCir: vi.fn(),
  putCir: vi.fn(),
  putCirBoard: vi.fn(),
}))

const mockGetCir = vi.mocked(getCir)

function fakeState(text: string, exists = true): CirState {
  return {
    exists,
    text,
    parse_error: null,
    board: null,
    validation: null,
    bom: [],
    sourcing: [],
  }
}

/** A stub `CirWriter` whose calls and exposed state are controllable. */
function makeWriter(overrides: Partial<CirWriter> = {}): CirWriter {
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
  vi.clearAllMocks()
})

describe('CirEditor', () => {
  it('loads the working CIR on mount and shows its text', async () => {
    mockGetCir.mockResolvedValue(fakeState('name: demo\n'))

    render(<CirEditor writer={makeWriter()} />)

    const textarea = await screen.findByLabelText('CIR YAML editor')
    await waitFor(() => expect(textarea).toHaveValue('name: demo\n'))
    expect(mockGetCir).toHaveBeenCalledTimes(1)
  })

  it('feeds every keystroke through writer.enqueueText', async () => {
    mockGetCir.mockResolvedValue(fakeState('', false))
    const writer = makeWriter()

    render(<CirEditor writer={writer} />)
    const textarea = await screen.findByLabelText('CIR YAML editor')

    fireEvent.change(textarea, { target: { value: 'edited: yes\n' } })

    expect(writer.enqueueText).toHaveBeenCalledWith('edited: yes\n')
  })

  it('shows the saving status while the writer is in flight', async () => {
    mockGetCir.mockResolvedValue(fakeState('x', true))

    render(<CirEditor writer={makeWriter({ status: 'saving' })} />)

    await waitFor(() =>
      expect(screen.getByText('Saving…')).toBeInTheDocument(),
    )
  })

  it('surfaces a writer error in the status line', async () => {
    mockGetCir.mockResolvedValue(fakeState('x', true))

    render(
      <CirEditor
        writer={makeWriter({ status: 'error', error: 'backend down' })}
      />,
    )

    await waitFor(() =>
      expect(screen.getByText('backend down')).toBeInTheDocument(),
    )
  })

  it('surfaces an error when the initial load fails', async () => {
    mockGetCir.mockRejectedValue(new Error('network down'))

    render(<CirEditor writer={makeWriter()} />)

    expect(await screen.findByText(/network down/)).toBeInTheDocument()
  })

  it('reloads from disk when reloadKey changes (G2-T6)', async () => {
    mockGetCir.mockResolvedValueOnce(fakeState('name: before\n'))
    const onState = vi.fn()
    const writer = makeWriter()

    const { rerender } = render(
      <CirEditor writer={writer} onState={onState} reloadKey={0} />,
    )
    const textarea = await screen.findByLabelText('CIR YAML editor')
    await waitFor(() => expect(textarea).toHaveValue('name: before\n'))

    mockGetCir.mockResolvedValueOnce(fakeState('name: after\n'))
    rerender(<CirEditor writer={writer} onState={onState} reloadKey={1} />)

    await waitFor(() => expect(textarea).toHaveValue('name: after\n'))
    expect(mockGetCir).toHaveBeenCalledTimes(2)
    expect(onState).toHaveBeenLastCalledWith(fakeState('name: after\n'))
  })

  it('does not reload when reloadKey is unchanged across a rerender', async () => {
    mockGetCir.mockResolvedValue(fakeState('name: stable\n'))
    const writer = makeWriter()

    const { rerender } = render(<CirEditor writer={writer} reloadKey={3} />)
    const textarea = await screen.findByLabelText('CIR YAML editor')
    await waitFor(() => expect(textarea).toHaveValue('name: stable\n'))

    rerender(<CirEditor writer={writer} reloadKey={3} />)
    expect(mockGetCir).toHaveBeenCalledTimes(1)
  })
})
