import { useState, type ChangeEvent } from 'react'
import { type Signoff, type SignoffPatch } from '../api/client'
import type { CirWriter } from '../cir/useCirWriter'

interface SignoffPanelProps {
  /** Current sign-off state from the parsed working CIR. */
  signoff: Signoff
  /** Shared single-flight writer. */
  writer: CirWriter
  /** Called after a successful PATCH so the parent reloads the editor. */
  onSignoffChanged?: () => void
}

/**
 * Human-only sign-off controls (SPEC-1 G4 + CLAUDE.md sign-off rule).
 *
 * The four M4 review gates (RF / DDR / BGA fanout) plus reviewer +
 * reviewed-at metadata. The agent's only path to flip these flags is to
 * Write the CIR file — and that path is already routed through the
 * approval gate (G2 `is_cir_write`). This panel is the explicit human
 * surface; toggling here issues a focused PATCH through the shared
 * single-flight writer so a sign-off flip can never race a text autosave.
 */
export function SignoffPanel({
  signoff,
  writer,
  onSignoffChanged,
}: SignoffPanelProps) {
  // Local draft so the user can toggle several flags + edit reviewer
  // before committing. Reset whenever the upstream signoff prop changes
  // (parent re-keys this panel on cirReload bumps).
  const [draft, setDraft] = useState<Signoff>(signoff)

  const update = (patch: Partial<Signoff>) => setDraft({ ...draft, ...patch })

  // Build a SignoffPatch containing only the fields the user actually
  // changed — the backend uses `exclude_unset` semantics, so an unset
  // field never overwrites the on-disk value.
  function buildPatch(): SignoffPatch {
    const patch: SignoffPatch = {}
    if (draft.rf_reviewed !== signoff.rf_reviewed)
      patch.rf_reviewed = draft.rf_reviewed
    if (draft.ddr_reviewed !== signoff.ddr_reviewed)
      patch.ddr_reviewed = draft.ddr_reviewed
    if (draft.bga_fanout_reviewed !== signoff.bga_fanout_reviewed)
      patch.bga_fanout_reviewed = draft.bga_fanout_reviewed
    if (draft.reviewer !== signoff.reviewer)
      patch.reviewer = draft.reviewer
    if (draft.reviewed_at !== signoff.reviewed_at)
      patch.reviewed_at = draft.reviewed_at
    return patch
  }

  async function save() {
    const patch = buildPatch()
    if (Object.keys(patch).length === 0) return
    await writer.writeSignoff(patch)
    onSignoffChanged?.()
  }

  const dirty =
    draft.rf_reviewed !== signoff.rf_reviewed ||
    draft.ddr_reviewed !== signoff.ddr_reviewed ||
    draft.bga_fanout_reviewed !== signoff.bga_fanout_reviewed ||
    draft.reviewer !== signoff.reviewer ||
    draft.reviewed_at !== signoff.reviewed_at

  return (
    <section className="signoff" aria-label="Sign-off">
      <header className="signoff__header">
        <h3 className="signoff__title">Sign-off</h3>
        <span className="signoff__hint">human-only</span>
      </header>

      <div className="signoff__gates">
        <SignoffCheckbox
          id="signoff-rf"
          label="RF reviewed"
          checked={draft.rf_reviewed}
          onChange={(value) => update({ rf_reviewed: value })}
        />
        <SignoffCheckbox
          id="signoff-ddr"
          label="DDR reviewed"
          checked={draft.ddr_reviewed}
          onChange={(value) => update({ ddr_reviewed: value })}
        />
        <SignoffCheckbox
          id="signoff-bga"
          label="BGA fanout reviewed"
          checked={draft.bga_fanout_reviewed}
          onChange={(value) => update({ bga_fanout_reviewed: value })}
        />
      </div>

      <div className="signoff__meta">
        <label className="signoff__field">
          <span className="signoff__field-label">Reviewer</span>
          <input
            type="text"
            className="signoff__field-input"
            aria-label="signoff-reviewer"
            value={draft.reviewer ?? ''}
            onChange={(event) =>
              update({
                reviewer:
                  event.target.value === '' ? null : event.target.value,
              })
            }
          />
        </label>
        <label className="signoff__field">
          <span className="signoff__field-label">Reviewed at</span>
          <input
            type="text"
            className="signoff__field-input"
            aria-label="signoff-reviewed-at"
            placeholder="YYYY-MM-DD"
            value={draft.reviewed_at ?? ''}
            onChange={(event) =>
              update({
                reviewed_at:
                  event.target.value === '' ? null : event.target.value,
              })
            }
          />
        </label>
      </div>

      <div className="signoff__bar">
        <span className={`signoff__status signoff__status--${writer.status}`}>
          {writer.status === 'saving'
            ? 'Saving…'
            : writer.status === 'error'
              ? `Error: ${writer.error ?? 'unknown'}`
              : dirty
                ? 'Unsaved changes'
                : 'Saved'}
        </span>
        <button
          type="button"
          className="signoff__save"
          onClick={() => void save()}
          disabled={!dirty || writer.status === 'saving'}
        >
          Save sign-off
        </button>
      </div>
    </section>
  )
}

interface SignoffCheckboxProps {
  id: string
  label: string
  checked: boolean
  onChange: (next: boolean) => void
}

function SignoffCheckbox({ id, label, checked, onChange }: SignoffCheckboxProps) {
  return (
    <label className="signoff__gate" htmlFor={id}>
      <input
        id={id}
        type="checkbox"
        aria-label={id}
        checked={checked}
        onChange={(event: ChangeEvent<HTMLInputElement>) =>
          onChange(event.target.checked)
        }
      />
      <span>{label}</span>
    </label>
  )
}
