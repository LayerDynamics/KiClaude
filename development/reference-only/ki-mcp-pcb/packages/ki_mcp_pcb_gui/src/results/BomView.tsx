import type { BOMRow } from '../api/client'

interface BomViewProps {
  /** BOM rows from the working CIR's parsed Board (CirState.bom). */
  bom: BOMRow[]
}

/**
 * Bill-of-materials view (SPEC-1 FR-7). Groups components by
 * MPN+footprint+value (the backend's `build_bom_rows` already does the
 * grouping) and shows the JLCPCB-style columns.
 */
export function BomView({ bom }: BomViewProps) {
  if (bom.length === 0) return null
  const totalParts = bom.reduce((sum, row) => sum + row.quantity, 0)
  return (
    <div className="results">
      <div className="results__head">
        BOM — {bom.length} line(s) / {totalParts} part(s)
      </div>
      <table className="results__table">
        <thead>
          <tr>
            <th>Qty</th>
            <th>Designator</th>
            <th>Value / comment</th>
            <th>MPN</th>
            <th>LCSC</th>
            <th>Footprint</th>
          </tr>
        </thead>
        <tbody>
          {bom.map((row) => (
            <tr key={`${row.mpn}|${row.footprint}|${row.designator}`}>
              <td>{row.quantity}</td>
              <td>{row.designator}</td>
              <td>{row.comment}</td>
              <td>{row.mpn}</td>
              <td>{row.lcsc ?? '—'}</td>
              <td>{row.footprint}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
