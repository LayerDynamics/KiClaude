import { useState } from 'react'
import { ApiError, type ParseIntentResponse, parseIntent } from '../api/client'
import type { CirWriter } from '../cir/useCirWriter'

interface IntentDialogProps {
  writer: CirWriter
  /** Controls visibility — parent owns the open state. */
  open: boolean
  /** Called when the user dismisses the dialog (× / Cancel / after Accept). */
  onClose: () => void
  /** Called once the working CIR has been written. */
  onAccepted?: () => void
}

type DialogState =
  | { kind: 'editing' }
  | { kind: 'generating' }
  | { kind: 'ready'; draft: ParseIntentResponse }
  | { kind: 'unavailable'; detail: string }
  | { kind: 'error'; detail: string }
  | { kind: 'accepting' }

/**
 * Natural-language → CIR dialog (SPEC-1 FR-5).
 *
 * Steps: user describes the board → Generate calls ``POST /api/parse_intent``
 * → the draft YAML is previewed → Accept writes it as the working CIR via
 * the shared single-flight writer (so any in-flight text autosave drains
 * first, no clobbering).
 */
export function IntentDialog({
  writer,
  open,
  onClose,
  onAccepted,
}: IntentDialogProps) {
  const [prompt, setPrompt] = useState('')
  const [state, setState] = useState<DialogState>({ kind: 'editing' })

  if (!open) return null

  async function generate() {
    const text = prompt.trim()
    if (!text) return
    setState({ kind: 'generating' })
    try {
      const draft = await parseIntent(text)
      setState({ kind: 'ready', draft })
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 503) {
        setState({ kind: 'unavailable', detail: err.message })
      } else {
        const detail =
          err instanceof Error ? err.message : 'failed to generate the draft'
        setState({ kind: 'error', detail })
      }
    }
  }

  async function accept(draft: ParseIntentResponse) {
    setState({ kind: 'accepting' })
    try {
      writer.enqueueText(draft.draft_yaml)
      await writer.flush()
      onAccepted?.()
      onClose()
      // Reset for the next time the dialog opens.
      setPrompt('')
      setState({ kind: 'editing' })
    } catch (err: unknown) {
      const detail =
        err instanceof Error ? err.message : 'failed to write the working CIR'
      setState({ kind: 'error', detail })
    }
  }

  const generating = state.kind === 'generating'
  const accepting = state.kind === 'accepting'

  return (
    <div
      className="intent-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="New project from intent"
    >
      <div className="intent-dialog">
        <header className="intent-dialog__header">
          <h2 className="intent-dialog__title">New project from intent</h2>
          <button
            type="button"
            className="intent-dialog__close"
            aria-label="Close"
            onClick={onClose}
            disabled={generating || accepting}
          >
            ×
          </button>
        </header>

        <p className="intent-dialog__hint">
          Describe the board you want in plain English. Claude will produce
          a draft CIR YAML; review it, then Accept to save it as the working
          file.
        </p>

        <textarea
          className="intent-dialog__prompt"
          aria-label="intent-prompt"
          rows={4}
          value={prompt}
          placeholder="e.g. ESP32-S3 dev board with USB-C, 3V3 LDO, a status LED, and one decoupling cap per supply pin"
          onChange={(event) => setPrompt(event.target.value)}
          disabled={generating || accepting}
        />

        <div className="intent-dialog__actions">
          <button
            type="button"
            className="intent-dialog__generate"
            onClick={() => void generate()}
            disabled={
              prompt.trim() === '' ||
              generating ||
              accepting ||
              state.kind === 'ready'
            }
          >
            {generating ? 'Generating…' : 'Generate'}
          </button>
        </div>

        {state.kind === 'unavailable' && (
          <div className="intent-dialog__notice intent-dialog__notice--unavailable">
            <strong>Anthropic not configured.</strong> {state.detail}
          </div>
        )}

        {state.kind === 'error' && (
          <div className="intent-dialog__notice intent-dialog__notice--error">
            {state.detail}
          </div>
        )}

        {state.kind === 'ready' && (
          <div className="intent-dialog__preview">
            <div className="intent-dialog__preview-head">
              <strong>Draft:</strong> {state.draft.board.name}
            </div>
            <textarea
              className="intent-dialog__yaml"
              aria-label="intent-draft-yaml"
              readOnly
              spellCheck={false}
              value={state.draft.draft_yaml}
              rows={14}
            />
            <div className="intent-dialog__actions">
              <button
                type="button"
                className="intent-dialog__discard"
                onClick={() => setState({ kind: 'editing' })}
              >
                Discard
              </button>
              <button
                type="button"
                className="intent-dialog__accept"
                onClick={() => void accept(state.draft)}
              >
                Accept as working CIR
              </button>
            </div>
          </div>
        )}

        {accepting && (
          <div className="intent-dialog__notice">
            Writing the working CIR…
          </div>
        )}
      </div>
    </div>
  )
}
