import type { CirState } from '../api/client'

interface SourcingViewProps {
  sourcing: CirState['sourcing']
}

/** A compact table of each component's sourcing status (registry / JLC / missing). */
export function SourcingView({ sourcing }: SourcingViewProps) {
  if (sourcing.length === 0) {
    return null
  }
  return (
    <div className="results">
      <div className="results__head">Sourcing — {sourcing.length} part(s)</div>
      <table className="results__table">
        <thead>
          <tr>
            <th>Refdes</th>
            <th>MPN</th>
            <th>Status</th>
            <th>LCSC</th>
          </tr>
        </thead>
        <tbody>
          {sourcing.map((entry, index) => (
            <tr key={index}>
              <td>{String(entry.refdes ?? '')}</td>
              <td>{String(entry.mpn ?? '')}</td>
              <td>{String(entry.status ?? '')}</td>
              <td>{entry.lcsc != null ? String(entry.lcsc) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
