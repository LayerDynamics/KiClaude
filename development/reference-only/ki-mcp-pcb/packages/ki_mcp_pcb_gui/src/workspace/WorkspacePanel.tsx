import { useEffect, useState } from 'react'
import { ApiError, type WorkspaceState, getWorkspace, setWorkspace } from '../api/client'

const SOURCE_LABEL: Record<WorkspaceState['source'], string> = {
  env: 'set by KIMP_GUI_WORKDIR',
  persisted: 'remembered from last session',
  default: 'default ./gui-workspace',
}

/**
 * Workspace header control (SPEC-1 G4): shows the current working
 * directory + how the backend resolved it, and lets the user pick a new
 * absolute path. A successful switch reloads the page so every pane
 * re-mounts against the fresh working directory (CirEditor, the form,
 * chat, build artifacts) — the only safe way to invalidate every cached
 * in-flight piece of state at once.
 */
export function WorkspacePanel() {
  const [workspace, setWorkspaceState] = useState<WorkspaceState | null>(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    getWorkspace()
      .then((state) => {
        if (cancelled) return
        setWorkspaceState(state)
        setDraft(state.path)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function save() {
    if (!draft.trim() || saving) return
    setSaving(true)
    setError(null)
    try {
      await setWorkspace(draft.trim())
      // Reload so every component re-fetches against the new working dir.
      window.location.reload()
    } catch (err: unknown) {
      setError(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err),
      )
    } finally {
      setSaving(false)
    }
  }

  if (workspace === null && error === null) {
    return <div className="workspace workspace--loading">Loading workspace…</div>
  }

  return (
    <div className="workspace">
      <span className="workspace__label">Workspace</span>
      <input
        type="text"
        className="workspace__input"
        aria-label="workspace-path"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        spellCheck={false}
        readOnly={workspace?.source === 'env'}
        title={
          workspace?.source === 'env'
            ? 'KIMP_GUI_WORKDIR is set; unset it to switch in the GUI.'
            : undefined
        }
      />
      {workspace && (
        <span className={`workspace__source workspace__source--${workspace.source}`}>
          {SOURCE_LABEL[workspace.source]}
        </span>
      )}
      <button
        type="button"
        className="workspace__save"
        onClick={() => void save()}
        disabled={
          saving ||
          workspace?.source === 'env' ||
          draft.trim() === '' ||
          (workspace !== null && draft.trim() === workspace.path)
        }
      >
        {saving ? 'Switching…' : 'Open'}
      </button>
      {error && <span className="workspace__error">{error}</span>}
    </div>
  )
}
