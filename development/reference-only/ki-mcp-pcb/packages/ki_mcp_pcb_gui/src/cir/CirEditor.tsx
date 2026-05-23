import { useEffect, useRef, useState } from 'react'
import { type CirState, getCir } from '../api/client'
import type { CirWriter } from './useCirWriter'

type DisplayStatus = 'loading' | 'idle' | 'saving' | 'saved' | 'error'

const STATUS_LABEL: Record<DisplayStatus, string> = {
  loading: 'Loading…',
  idle: 'No working CIR yet',
  saving: 'Saving…',
  saved: 'Saved',
  error: 'Error',
}

interface CirEditorProps {
  /** Single-flight writer shared with the form editor (SPEC-1 G3-T6). */
  writer: CirWriter
  /** Called with the latest CIR state after the initial load. */
  onState?: (state: CirState) => void
  /**
   * Bump to force a reload from `GET /api/cir` — used when the co-pilot or
   * the form editor changes the working CIR (SPEC-1 FR-17, G3 sync).
   */
  reloadKey?: number
}

/**
 * The working-CIR text editor. Loads `GET /api/cir` on mount and feeds every
 * keystroke into the shared single-flight writer, which debounces, dedupes
 * and serialises text writes against form-mode writes. A change to
 * `reloadKey` reloads the on-disk file.
 */
export function CirEditor({ writer, onState, reloadKey = 0 }: CirEditorProps) {
  const [text, setText] = useState('')
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [exists, setExists] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  // `onState` is read through a ref so changing it doesn't re-fire the load.
  const onStateRef = useRef(onState)
  useEffect(() => {
    onStateRef.current = onState
  }, [onState])

  // Load on mount, and again whenever `reloadKey` changes — the on-disk
  // file is the source of truth, so a reload deliberately replaces the
  // editor's text (in form-driven reloads, with the canonical YAML).
  useEffect(() => {
    let cancelled = false
    getCir()
      .then((state) => {
        if (cancelled) return
        setText(state.text)
        setExists(state.exists)
        setLoadError(null)
        setLoadedOnce(true)
        onStateRef.current?.(state)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLoadError(err instanceof Error ? err.message : String(err))
        setLoadedOnce(true)
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  function handleChange(next: string) {
    setText(next)
    writer.enqueueText(next)
  }

  // Map the writer's status (idle/saving/error) + the load lifecycle to a
  // single display state for the status line.
  const display: DisplayStatus = !loadedOnce
    ? 'loading'
    : loadError !== null
      ? 'error'
      : writer.status === 'saving'
        ? 'saving'
        : writer.status === 'error'
          ? 'error'
          : exists
            ? 'saved'
            : 'idle'

  const message = loadError ?? writer.error

  return (
    <div className="cir-editor">
      <div className={`cir-editor__status cir-editor__status--${display}`}>
        <strong>CIR</strong>
        <span>{STATUS_LABEL[display]}</span>
        {message && <span className="cir-editor__message">{message}</span>}
      </div>
      <textarea
        className="cir-editor__text"
        aria-label="CIR YAML editor"
        spellCheck={false}
        value={text}
        placeholder="# Paste or write a CIR YAML spec here…"
        onChange={(event) => handleChange(event.target.value)}
      />
    </div>
  )
}
