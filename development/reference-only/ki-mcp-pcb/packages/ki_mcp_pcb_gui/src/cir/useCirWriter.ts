import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type Board,
  type CirState,
  type SignoffPatch,
  patchSignoff,
  putCir,
  putCirBoard,
} from '../api/client'

/** How long autosave waits after the last keystroke before persisting text. */
const TEXT_DEBOUNCE_MS = 800

/** Lifecycle of the single in-flight CIR write. */
export type CirWriteStatus = 'idle' | 'saving' | 'error'

interface UseCirWriterArgs {
  /** Called with the fresh `CirState` after every successful write. */
  onState: (state: CirState) => void
}

export interface CirWriter {
  status: CirWriteStatus
  error: string | null
  /**
   * Enqueue a debounced text write. Repeated calls within the debounce
   * collapse into a single write of the latest text.
   */
  enqueueText: (text: string) => void
  /**
   * Force any pending debounced text write to fire now, then resolve when
   * the in-flight queue is drained. Safe to call when nothing is pending.
   */
  flush: () => Promise<void>
  /**
   * Persist a structured Board. Flushes any pending text write first, so a
   * form save never clobbers in-flight typing (SPEC-1 G3 single-flight).
   */
  writeBoard: (board: Board) => Promise<void>
  /**
   * Apply a partial sign-off PATCH. Goes through the same single-flight
   * queue: flush pending text first, then PATCH. SPEC-1 G4 + CLAUDE.md:
   * only a human may flip a ``Board.signoff.*`` flag — the agent's only
   * sign-off path is Write/Edit of the CIR file, which is already gated.
   */
  writeSignoff: (patch: SignoffPatch) => Promise<void>
}

/**
 * Single-flight queue for CIR writes (SPEC-1 G3).
 *
 * Text autosave (CirEditor) and form save (BoardForm) both call into the
 * same queue, so the two modes can never race against each other. Writes
 * land in call order; on success `onState` is called with the canonical
 * `CirState` the backend returned, so every view stays consistent.
 */
export function useCirWriter({ onState }: UseCirWriterArgs): CirWriter {
  const [status, setStatus] = useState<CirWriteStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  // The chain of write promises — each new write tails the previous so they
  // never run concurrently.
  const inflight = useRef<Promise<void>>(Promise.resolve())
  // The pending text-debounce timer + the most recent text it would write.
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingText = useRef<string | null>(null)
  // `onState` is read through a ref so it never re-creates the callbacks.
  const onStateRef = useRef(onState)
  useEffect(() => {
    onStateRef.current = onState
  }, [onState])

  const enqueueOp = useCallback(
    (op: () => Promise<CirState>): Promise<void> => {
      const next = inflight.current.then(async () => {
        setStatus('saving')
        setError(null)
        try {
          const state = await op()
          onStateRef.current(state)
          setStatus('idle')
        } catch (err: unknown) {
          setStatus('error')
          setError(err instanceof Error ? err.message : String(err))
        }
      })
      inflight.current = next
      return next
    },
    [],
  )

  const cancelTimer = useCallback(() => {
    if (debounceTimer.current !== null) {
      clearTimeout(debounceTimer.current)
      debounceTimer.current = null
    }
  }, [])

  const flush = useCallback((): Promise<void> => {
    cancelTimer()
    const text = pendingText.current
    pendingText.current = null
    if (text !== null) {
      return enqueueOp(() => putCir(text))
    }
    return inflight.current
  }, [cancelTimer, enqueueOp])

  const enqueueText = useCallback(
    (text: string) => {
      pendingText.current = text
      cancelTimer()
      debounceTimer.current = setTimeout(() => {
        debounceTimer.current = null
        const queued = pendingText.current
        pendingText.current = null
        if (queued !== null) {
          void enqueueOp(() => putCir(queued))
        }
      }, TEXT_DEBOUNCE_MS)
    },
    [cancelTimer, enqueueOp],
  )

  const writeBoard = useCallback(
    async (board: Board): Promise<void> => {
      // Flush pending text first so the form save can never overwrite a
      // typed-but-unsaved buffer.
      await flush()
      await enqueueOp(() => putCirBoard(board))
    },
    [enqueueOp, flush],
  )

  const writeSignoff = useCallback(
    async (patch: SignoffPatch): Promise<void> => {
      // Same single-flight discipline as writeBoard — pending text drains
      // first so a sign-off toggle can't clobber a typing autosave.
      await flush()
      await enqueueOp(() => patchSignoff(patch))
    },
    [enqueueOp, flush],
  )

  // Cancel a pending debounce on unmount.
  useEffect(() => () => cancelTimer(), [cancelTimer])

  return { status, error, enqueueText, flush, writeBoard, writeSignoff }
}
