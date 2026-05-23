import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Signoff } from '../../api/client'
import type { CirWriter } from '../../cir/useCirWriter'
import { SignoffPanel } from '../SignoffPanel'

function clean(): Signoff {
  return {
    rf_reviewed: false,
    ddr_reviewed: false,
    bga_fanout_reviewed: false,
    reviewer: null,
    reviewed_at: null,
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
  vi.clearAllMocks()
})

describe('SignoffPanel', () => {
  it('renders all three gate checkboxes + reviewer fields', () => {
    render(<SignoffPanel signoff={clean()} writer={stubWriter()} />)
    expect(screen.getByLabelText('signoff-rf')).not.toBeChecked()
    expect(screen.getByLabelText('signoff-ddr')).not.toBeChecked()
    expect(screen.getByLabelText('signoff-bga')).not.toBeChecked()
    expect(screen.getByLabelText('signoff-reviewer')).toHaveValue('')
    expect(screen.getByLabelText('signoff-reviewed-at')).toHaveValue('')
    expect(screen.getByText('human-only')).toBeInTheDocument()
  })

  it('Save is disabled until the user toggles something', () => {
    render(<SignoffPanel signoff={clean()} writer={stubWriter()} />)
    expect(screen.getByRole('button', { name: 'Save sign-off' })).toBeDisabled()
    expect(screen.getByText('Saved')).toBeInTheDocument()
  })

  it('shows the unsaved-changes status once a gate is flipped', () => {
    render(<SignoffPanel signoff={clean()} writer={stubWriter()} />)
    fireEvent.click(screen.getByLabelText('signoff-rf'))
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save sign-off' })).not.toBeDisabled()
  })

  it('PATCHes only the fields the user actually changed', async () => {
    const writer = stubWriter()
    const onSignoffChanged = vi.fn()
    render(
      <SignoffPanel
        signoff={clean()}
        writer={writer}
        onSignoffChanged={onSignoffChanged}
      />,
    )

    fireEvent.click(screen.getByLabelText('signoff-rf'))
    fireEvent.change(screen.getByLabelText('signoff-reviewer'), {
      target: { value: 'rdo' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save sign-off' }))

    await waitFor(() => expect(writer.writeSignoff).toHaveBeenCalledOnce())
    const patch = vi.mocked(writer.writeSignoff).mock.calls[0][0]
    expect(patch).toEqual({ rf_reviewed: true, reviewer: 'rdo' })
    // ddr_reviewed / bga_fanout_reviewed / reviewed_at were NOT sent —
    // backend's exclude_unset semantics will preserve their on-disk values.
    expect('ddr_reviewed' in patch).toBe(false)
    expect('bga_fanout_reviewed' in patch).toBe(false)
    expect('reviewed_at' in patch).toBe(false)
    await waitFor(() => expect(onSignoffChanged).toHaveBeenCalledOnce())
  })

  it('treats an empty reviewer string as null in the patch', async () => {
    const initial: Signoff = { ...clean(), reviewer: 'rdo' }
    const writer = stubWriter()
    render(<SignoffPanel signoff={initial} writer={writer} />)

    fireEvent.change(screen.getByLabelText('signoff-reviewer'), {
      target: { value: '' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save sign-off' }))

    await waitFor(() => expect(writer.writeSignoff).toHaveBeenCalledOnce())
    expect(vi.mocked(writer.writeSignoff).mock.calls[0][0]).toEqual({
      reviewer: null,
    })
  })

  it('disables Save while a write is in flight', () => {
    render(
      <SignoffPanel signoff={clean()} writer={stubWriter({ status: 'saving' })} />,
    )
    fireEvent.click(screen.getByLabelText('signoff-rf'))
    expect(screen.getByRole('button', { name: 'Save sign-off' })).toBeDisabled()
    expect(screen.getByText('Saving…')).toBeInTheDocument()
  })

  it('surfaces a writer error in the status line', () => {
    render(
      <SignoffPanel
        signoff={clean()}
        writer={stubWriter({ status: 'error', error: 'backend down' })}
      />,
    )
    expect(screen.getByText(/backend down/)).toBeInTheDocument()
  })
})
