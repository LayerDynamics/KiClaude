import { useState } from 'react'
import type { Board } from '../api/client'
import type { CirWriter } from '../cir/useCirWriter'
import { ComponentsForm } from './ComponentsForm'
import { FabForm } from './FabForm'
import { NetsForm } from './NetsForm'
import { StackupForm } from './StackupForm'

interface BoardFormProps {
  /** Initial board — re-initialise the local draft by changing the parent `key`. */
  board: Board
  /** Single-flight writer shared with the text editor (SPEC-1 G3-T6). */
  writer: CirWriter
  /** Called after a successful save so the parent can bump the editor reload. */
  onCirChanged?: () => void
}

/**
 * The structured Board editor (SPEC-1 G3, FR-3).
 *
 * Local draft state is mutated by the four sub-forms; an explicit Save
 * flushes any pending text autosave (via `writer.writeBoard` → `flush` →
 * `putCirBoard`) so the form save never clobbers in-flight typing.
 */
export function BoardForm({ board, writer, onCirChanged }: BoardFormProps) {
  // Local draft mirrors the upstream board on mount; the parent remounts us
  // (via `key`) when the on-disk board changes for reasons outside the
  // form (text save, co-pilot edit, reloadKey bump).
  const [draft, setDraft] = useState<Board>(board)

  async function save() {
    await writer.writeBoard(draft)
    onCirChanged?.()
  }

  return (
    <div className="board-form">
      <ComponentsForm board={draft} onChange={setDraft} />
      <NetsForm board={draft} onChange={setDraft} />
      <StackupForm board={draft} onChange={setDraft} />
      <FabForm board={draft} onChange={setDraft} />
      <div className="board-form__bar">
        <span className={`board-form__status board-form__status--${writer.status}`}>
          {writer.status === 'saving'
            ? 'Saving…'
            : writer.status === 'error'
              ? `Error: ${writer.error ?? 'unknown'}`
              : 'Saved'}
        </span>
        <button
          type="button"
          className="board-form__save"
          onClick={() => {
            void save()
          }}
          disabled={writer.status === 'saving'}
        >
          Save board
        </button>
      </div>
    </div>
  )
}
