import { useEffect, useState } from 'react'
import {
  ApiError,
  type ReturnPathCheckResponse,
  returnPathCheck,
} from '../api/client'

interface ReturnPathViewProps {
  refreshKey: number
}

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ReturnPathCheckResponse }
  | { kind: 'error'; detail: string }

/**
 * Return-path check (CIR090) over the working CIR (SPEC-1 FR-11): which
 * high-speed nets exist and which still lack a `reference_plane`.
 */
export function ReturnPathView({ refreshKey }: ReturnPathViewProps) {
  const [state, setState] = useState<ViewState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    returnPathCheck()
      .then((data) => {
        if (!cancelled) setState({ kind: 'ready', data })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const detail =
          err instanceof ApiError ? err.message : (err as Error).message
        setState({ kind: 'error', detail })
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  if (state.kind === 'loading') {
    return (
      <div className="results">
        <div className="results__head">Return path</div>
        <div className="pane__placeholder">Loading…</div>
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="results">
        <div className="results__head">Return path</div>
        <div className="results__error">{state.detail}</div>
      </div>
    )
  }

  const { data } = state
  const verdict = data.ok ? 'ok' : 'warn'
  return (
    <div className="results">
      <div className="results__head">
        Return path — <span className={`results__badge results__badge--${verdict}`}>{verdict}</span>
      </div>
      {data.issues.length > 0 && (
        <ul className="results__issues">
          {data.issues.map((issue, index) => (
            <li
              key={`${issue.code}-${index}`}
              className={`issue issue--${issue.severity}`}
            >
              <span className="issue__code">{issue.code}</span>
              <span>{issue.message}</span>
              {issue.where && <span className="issue__where">{issue.where}</span>}
            </li>
          ))}
        </ul>
      )}
      {data.high_speed_nets.length === 0 ? (
        <div className="results__sub">No high-speed nets in this design.</div>
      ) : (
        <table className="results__table">
          <thead>
            <tr>
              <th>Net</th>
              <th>Class</th>
              <th>Reference plane</th>
            </tr>
          </thead>
          <tbody>
            {data.high_speed_nets.map((row) => (
              <tr key={row.net}>
                <td>{row.net}</td>
                <td>{row.net_class}</td>
                <td>{row.reference_plane ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
