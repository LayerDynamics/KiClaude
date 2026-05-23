import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, parseIntent } from '../../api/client'
import type { CirWriter } from '../../cir/useCirWriter'
import { IntentDialog } from '../IntentDialog'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>(
    '../../api/client',
  )
  return { ...actual, parseIntent: vi.fn() }
})

const mockParseIntent = vi.mocked(parseIntent)

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

const A_DRAFT = {
  board: {
    cir_version: '0.4',
    name: 'demo',
    description: null,
    components: [],
    nets: [],
    constraints: [],
  },
  draft_yaml: 'cir_version: "0.4"\nname: demo\n',
}

beforeEach(() => {
  mockParseIntent.mockReset()
})

describe('IntentDialog', () => {
  it('renders nothing when open=false', () => {
    const { container } = render(
      <IntentDialog writer={makeWriter()} open={false} onClose={() => {}} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the prompt textarea and a disabled Generate when empty', () => {
    render(
      <IntentDialog writer={makeWriter()} open={true} onClose={() => {}} />,
    )
    expect(screen.getByLabelText('intent-prompt')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled()
  })

  it('calls parseIntent with the typed prompt and previews the draft', async () => {
    mockParseIntent.mockResolvedValueOnce(A_DRAFT)
    render(
      <IntentDialog writer={makeWriter()} open={true} onClose={() => {}} />,
    )

    fireEvent.change(screen.getByLabelText('intent-prompt'), {
      target: { value: 'an ESP32-S3 dev board' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() =>
      expect(mockParseIntent).toHaveBeenCalledWith('an ESP32-S3 dev board'),
    )
    expect(
      await screen.findByLabelText('intent-draft-yaml'),
    ).toHaveValue(A_DRAFT.draft_yaml)
    // The preview head shows "Draft: <board name>" (the name is a sibling
    // text node, so we assert via the element's full textContent).
    const head = screen.getByText('Draft:').closest('.intent-dialog__preview-head')
    expect(head?.textContent).toContain('demo')
  })

  it('Accept writes the draft via the writer and closes', async () => {
    mockParseIntent.mockResolvedValueOnce(A_DRAFT)
    const writer = makeWriter()
    const onClose = vi.fn()
    const onAccepted = vi.fn()

    render(
      <IntentDialog
        writer={writer}
        open={true}
        onClose={onClose}
        onAccepted={onAccepted}
      />,
    )

    fireEvent.change(screen.getByLabelText('intent-prompt'), {
      target: { value: 'something' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await screen.findByLabelText('intent-draft-yaml')

    fireEvent.click(screen.getByRole('button', { name: 'Accept as working CIR' }))

    await waitFor(() => expect(writer.enqueueText).toHaveBeenCalledWith(A_DRAFT.draft_yaml))
    await waitFor(() => expect(writer.flush).toHaveBeenCalledOnce())
    await waitFor(() => expect(onAccepted).toHaveBeenCalledOnce())
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  })

  it('Discard returns to the editing state without writing', async () => {
    mockParseIntent.mockResolvedValueOnce(A_DRAFT)
    const writer = makeWriter()
    render(
      <IntentDialog writer={writer} open={true} onClose={() => {}} />,
    )

    fireEvent.change(screen.getByLabelText('intent-prompt'), {
      target: { value: 'x' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await screen.findByLabelText('intent-draft-yaml')

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(screen.queryByLabelText('intent-draft-yaml')).not.toBeInTheDocument()
    expect(writer.enqueueText).not.toHaveBeenCalled()
  })

  it('renders the unavailable notice on a 503 from parseIntent', async () => {
    mockParseIntent.mockRejectedValueOnce(
      new ApiError(503, 'Set ANTHROPIC_API_KEY'),
    )
    render(
      <IntentDialog writer={makeWriter()} open={true} onClose={() => {}} />,
    )
    fireEvent.change(screen.getByLabelText('intent-prompt'), {
      target: { value: 'x' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))

    expect(
      await screen.findByText(/Set ANTHROPIC_API_KEY/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Anthropic not configured/)).toBeInTheDocument()
  })

  it('renders a generic error on non-503 failures', async () => {
    mockParseIntent.mockRejectedValueOnce(
      new ApiError(400, 'model returned no YAML block'),
    )
    render(
      <IntentDialog writer={makeWriter()} open={true} onClose={() => {}} />,
    )
    fireEvent.change(screen.getByLabelText('intent-prompt'), {
      target: { value: 'x' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))

    expect(
      await screen.findByText('model returned no YAML block'),
    ).toBeInTheDocument()
  })

  it('Close dispatches onClose without writing anything', () => {
    const writer = makeWriter()
    const onClose = vi.fn()
    render(
      <IntentDialog writer={writer} open={true} onClose={onClose} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(writer.enqueueText).not.toHaveBeenCalled()
  })
})
