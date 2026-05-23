import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Board, FabTarget } from '../../api/client'
import { FabForm } from '../FabForm'

const A_FAB: FabTarget = {
  name: 'jlcpcb',
  min_trace_mm: 0.127,
  min_space_mm: 0.127,
  min_drill_mm: 0.2,
  min_annular_ring_mm: 0.13,
  layer_count: 2,
}

function makeBoard(): Board {
  return {
    cir_version: '0.4',
    name: 'demo',
    components: [],
    nets: [],
    constraints: [],
    fab: A_FAB,
  }
}

describe('FabForm', () => {
  it('renders all six fab fields with their current values', () => {
    render(<FabForm board={makeBoard()} onChange={() => {}} />)
    expect(screen.getByLabelText('fab-vendor')).toHaveValue('jlcpcb')
    expect(screen.getByLabelText('fab-layer-count')).toHaveValue(2)
    expect(screen.getByLabelText('fab-min-trace-mm')).toHaveValue(0.127)
    expect(screen.getByLabelText('fab-min-space-mm')).toHaveValue(0.127)
    expect(screen.getByLabelText('fab-min-drill-mm')).toHaveValue(0.2)
    expect(screen.getByLabelText('fab-min-annular-ring-mm')).toHaveValue(0.13)
  })

  it('switches the fab vendor through the enum select', () => {
    const onChange = vi.fn()
    render(<FabForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('fab-vendor'), {
      target: { value: 'oshpark' },
    })
    expect((onChange.mock.calls[0][0] as Board).fab?.name).toBe('oshpark')
  })

  it('updates min_trace_mm as a number', () => {
    const onChange = vi.fn()
    render(<FabForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('fab-min-trace-mm'), {
      target: { value: '0.1' },
    })
    expect((onChange.mock.calls[0][0] as Board).fab?.min_trace_mm).toBe(0.1)
  })

  it('updates layer_count as an integer', () => {
    const onChange = vi.fn()
    render(<FabForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('fab-layer-count'), {
      target: { value: '4' },
    })
    expect((onChange.mock.calls[0][0] as Board).fab?.layer_count).toBe(4)
  })

  it('falls back to the Pydantic defaults when board.fab is missing', () => {
    const empty: Board = {
      cir_version: '0.4',
      name: 'no-fab',
      components: [],
      nets: [],
      constraints: [],
    }
    render(<FabForm board={empty} onChange={() => {}} />)
    expect(screen.getByLabelText('fab-vendor')).toHaveValue('jlcpcb')
    expect(screen.getByLabelText('fab-layer-count')).toHaveValue(2)
  })
})
