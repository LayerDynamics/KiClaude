import { useEffect, useState } from 'react'
import { ApiError, type ImpedanceResponse, impedanceCheck } from '../api/client'

interface ImpedanceViewProps {
  /** Bump on cir_changed / build to re-fetch from the working CIR. */
  refreshKey: number
}

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ImpedanceResponse }
  | { kind: 'error'; detail: string }

/**
 * Per-net controlled-impedance table over the working CIR (SPEC-1 FR-11).
 *
 * Each row shows target Zo, the closed-form achievable Zo (microstrip /
 * differential / CPWG, picked from the net's geometry), and the trace
 * geometry that drove it.
 */
export function ImpedanceView({ refreshKey }: ImpedanceViewProps) {
  const [state, setState] = useState<ViewState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    impedanceCheck()
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
        <div className="results__head">Impedance</div>
        <div className="pane__placeholder">Loading…</div>
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="results">
        <div className="results__head">Impedance</div>
        <div className="results__error">{state.detail}</div>
      </div>
    )
  }
  if (state.data.rows.length === 0) {
    return (
      <div className="results">
        <div className="results__head">Impedance</div>
        <div className="pane__placeholder">
          No nets declare `target_impedance_ohm`.
        </div>
      </div>
    )
  }
  return (
    <div className="results">
      <div className="results__head">Impedance — {state.data.rows.length} net(s)</div>
      <table className="results__table">
        <thead>
          <tr>
            <th>Net</th>
            <th>Target (Ω)</th>
            <th>Achieved (Ω)</th>
            <th>Trace w (mm)</th>
            <th>Trace s (mm)</th>
            <th>CPWG gap (mm)</th>
            <th>Diff pair</th>
          </tr>
        </thead>
        <tbody>
          {state.data.rows.map((row) => {
            const diff =
              row.achieved_ohm !== null
                ? Math.abs(row.achieved_ohm - row.target_ohm) / row.target_ohm
                : null
            const severity =
              diff === null
                ? 'unknown'
                : diff > 0.2
                  ? 'bad'
                  : diff > 0.1
                    ? 'warn'
                    : 'ok'
            return (
              <tr key={row.net} className={`zo-row zo-row--${severity}`}>
                <td>{row.net}</td>
                <td>{row.target_ohm}</td>
                <td>
                  {row.achieved_ohm === null ? '—' : row.achieved_ohm.toFixed(2)}
                </td>
                <td>{row.trace_width_mm ?? '—'}</td>
                <td>{row.trace_spacing_mm ?? '—'}</td>
                <td>{row.cpwg_gap_mm ?? '—'}</td>
                <td>{row.diff_pair_with ?? '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
