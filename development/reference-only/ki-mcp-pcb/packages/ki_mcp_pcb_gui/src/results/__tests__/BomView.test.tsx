import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { BOMRow } from '../../api/client'
import { BomView } from '../BomView'

const ROWS: BOMRow[] = [
  {
    designator: 'C1,C2,C3',
    comment: '100nF X7R 0402',
    footprint: 'Capacitor_SMD:C_0402_1005Metric',
    mpn: 'CL05B104KO5NNNC',
    lcsc: 'C1525',
    quantity: 3,
  },
  {
    designator: 'U1',
    comment: 'ESP32-S3-WROOM-1',
    footprint: 'RF_Module:ESP32-S3-WROOM-1',
    mpn: 'ESP32-S3-WROOM-1',
    lcsc: null,
    quantity: 1,
  },
]

describe('BomView', () => {
  it('shows nothing when the BOM is empty', () => {
    const { container } = render(<BomView bom={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders one table row per BOM line with quantities and totals', () => {
    render(<BomView bom={ROWS} />)
    expect(screen.getByText(/BOM — 2 line\(s\) \/ 4 part\(s\)/)).toBeInTheDocument()
    expect(screen.getByText('C1,C2,C3')).toBeInTheDocument()
    expect(screen.getByText('100nF X7R 0402')).toBeInTheDocument()
    expect(screen.getByText('CL05B104KO5NNNC')).toBeInTheDocument()
    expect(screen.getByText('C1525')).toBeInTheDocument()
  })

  it('shows an em dash when LCSC is missing', () => {
    render(<BomView bom={[ROWS[1]]} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
