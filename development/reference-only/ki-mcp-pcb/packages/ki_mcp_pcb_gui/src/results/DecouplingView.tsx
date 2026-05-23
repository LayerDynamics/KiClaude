import { useEffect, useState } from 'react'
import {
  ApiError,
  type DecouplingCheckResponse,
  decouplingCheck,
} from '../api/client'

interface DecouplingViewProps {
  refreshKey: number
}

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: DecouplingCheckResponse }
  | { kind: 'error'; detail: string }

/**
 * Decoupling-coverage check (CIR030) over the working CIR (SPEC-1 FR-11):
 * the validator's verdict + the per-IC declaration list.
 */
export function DecouplingView({ refreshKey }: DecouplingViewProps) {
  const [state, setState] = useState<ViewState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    decouplingCheck()
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
        <div className="results__head">Decoupling</div>
        <div className="pane__placeholder">Loading…</div>
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="results">
        <div className="results__head">Decoupling</div>
        <div className="results__error">{state.detail}</div>
      </div>
    )
  }

  const verdict = state.data.ok ? 'ok' : 'fail'
  return (
    <div className="results">
      <div className="results__head">
        Decoupling — <span className={`results__badge results__badge--${verdict}`}>{verdict}</span>
      </div>
      {state.data.issues.length > 0 && (
        <ul className="results__issues">
          {state.data.issues.map((issue, index) => (
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
      <div className="results__sub">
        Components declaring decoupling pins:{' '}
        {state.data.ics_with_decoupling_declared.length === 0
          ? 'none'
          : state.data.ics_with_decoupling_declared.join(', ')}
      </div>
    </div>
  )
}
