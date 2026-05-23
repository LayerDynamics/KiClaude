import { useState, type ChangeEvent } from 'react'
import { ApiError, type DiffResponse, diffAgainstWorking } from '../api/client'

type ViewState =
  | { kind: 'empty' }
  | { kind: 'diffing'; filename: string }
  | { kind: 'ready'; filename: string; data: DiffResponse }
  | { kind: 'error'; detail: string }

/**
 * Diff an uploaded baseline CIR against the working CIR (SPEC-1 FR-10).
 *
 * The user picks a baseline (YAML/.ato); the server treats the baseline
 * as "before" and the working CIR as "after", returning the structured
 * BoardDiff.
 */
export function DiffView() {
  const [state, setState] = useState<ViewState>({ kind: 'empty' })

  async function onPick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    setState({ kind: 'diffing', filename: file.name })
    try {
      const data = await diffAgainstWorking(file)
      setState({ kind: 'ready', filename: file.name, data })
    } catch (err: unknown) {
      const detail =
        err instanceof ApiError ? err.message : (err as Error).message
      setState({ kind: 'error', detail })
    } finally {
      // Allow re-uploading the same file (browser won't fire change otherwise).
      event.target.value = ''
    }
  }

  return (
    <div className="results">
      <div className="results__head diff-head">
        <span>Diff vs baseline</span>
        <label className="diff-picker">
          <span className="diff-picker__btn">Choose baseline…</span>
          <input
            type="file"
            accept=".yaml,.yml,.ato"
            aria-label="diff-baseline"
            onChange={onPick}
          />
        </label>
      </div>
      {state.kind === 'empty' && (
        <div className="pane__placeholder">
          Pick a baseline CIR to diff against the working file.
        </div>
      )}
      {state.kind === 'diffing' && (
        <div className="pane__placeholder">
          Diffing {state.filename}…
        </div>
      )}
      {state.kind === 'error' && (
        <div className="results__error">{state.detail}</div>
      )}
      {state.kind === 'ready' && <DiffBody data={state.data} filename={state.filename} />}
    </div>
  )
}

interface DiffBodyProps {
  data: DiffResponse
  filename: string
}

function DiffBody({ data, filename }: DiffBodyProps) {
  if (data.identical) {
    return (
      <div className="results__sub">
        Working CIR is identical to <code>{filename}</code>.
      </div>
    )
  }
  return (
    <div className="diff-body">
      <div className="results__sub">
        Baseline: <code>{filename}</code> — {data.summary}
      </div>
      {data.name_changed && (
        <div className="diff-line">
          Name changed: <code>{data.name_changed[0]}</code> →{' '}
          <code>{data.name_changed[1]}</code>
        </div>
      )}
      <DiffList title="Components added" items={data.components_added} />
      <DiffList title="Components removed" items={data.components_removed} />
      {data.component_changes.length > 0 && (
        <details className="diff-section" open>
          <summary>Component changes ({data.component_changes.length})</summary>
          <table className="results__table">
            <thead>
              <tr>
                <th>Refdes</th>
                <th>Field</th>
                <th>Before</th>
                <th>After</th>
              </tr>
            </thead>
            <tbody>
              {data.component_changes.map((row, index) => (
                <tr key={`${row.refdes}-${row.field}-${index}`}>
                  <td>{row.refdes}</td>
                  <td>{row.field}</td>
                  <td>{row.left ?? '—'}</td>
                  <td>{row.right ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <DiffList title="Nets added" items={data.nets_added} />
      <DiffList title="Nets removed" items={data.nets_removed} />
      {data.net_changes.length > 0 && (
        <details className="diff-section" open>
          <summary>Net changes ({data.net_changes.length})</summary>
          <table className="results__table">
            <thead>
              <tr>
                <th>Net</th>
                <th>Field</th>
                <th>Before</th>
                <th>After</th>
              </tr>
            </thead>
            <tbody>
              {data.net_changes.map((row, index) => (
                <tr key={`${row.name}-${row.field}-${index}`}>
                  <td>{row.name}</td>
                  <td>{row.field}</td>
                  <td>{row.left}</td>
                  <td>{row.right}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}

interface DiffListProps {
  title: string
  items: string[]
}

function DiffList({ title, items }: DiffListProps) {
  if (items.length === 0) return null
  return (
    <div className="diff-section">
      <strong>{title} ({items.length}):</strong> {items.join(', ')}
    </div>
  )
}
