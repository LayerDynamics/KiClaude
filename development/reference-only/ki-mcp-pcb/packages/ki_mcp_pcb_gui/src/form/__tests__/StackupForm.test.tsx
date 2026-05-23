import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Board, Stackup } from '../../api/client'
import { StackupForm } from '../StackupForm'

function makeStackup(): Stackup {
  return {
    layers: [
      { name: 'F.Cu', kind: 'copper', thickness_mm: 0.035, material: null, er: null },
      { name: 'core', kind: 'dielectric', thickness_mm: 1.5, material: 'FR-4', er: 4.5 },
      { name: 'B.Cu', kind: 'copper', thickness_mm: 0.035, material: null, er: null },
    ],
    finished_thickness_mm: 1.6,
    controlled_impedance: false,
    power_plane_layers: [],
  }
}

function makeBoard(): Board {
  return {
    cir_version: '0.4',
    name: 'demo',
    components: [],
    nets: [],
    constraints: [],
    stackup: makeStackup(),
  }
}

describe('StackupForm', () => {
  it('renders each layer in order', () => {
    render(<StackupForm board={makeBoard()} onChange={() => {}} />)
    expect(screen.getByLabelText('layer-name-0')).toHaveValue('F.Cu')
    expect(screen.getByLabelText('layer-kind-1')).toHaveValue('dielectric')
    expect(screen.getByLabelText('layer-name-2')).toHaveValue('B.Cu')
  })

  it('renders the stackup scalar fields', () => {
    render(<StackupForm board={makeBoard()} onChange={() => {}} />)
    expect(screen.getByLabelText('finished-thickness-mm')).toHaveValue(1.6)
    expect(screen.getByLabelText('controlled-impedance')).not.toBeChecked()
  })

  it('updates the controlled-impedance flag', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('controlled-impedance'))
    const next = onChange.mock.calls[0][0] as Board
    expect(next.stackup?.controlled_impedance).toBe(true)
  })

  it('updates a layer thickness through the cell editor', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('layer-thickness-1'), {
      target: { value: '0.8' },
    })
    const next = onChange.mock.calls[0][0] as Board
    expect(next.stackup?.layers[1].thickness_mm).toBe(0.8)
  })

  it('switches a layer kind through the enum select', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('layer-kind-0'), {
      target: { value: 'soldermask' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).stackup?.layers[0].kind,
    ).toBe('soldermask')
  })

  it('moves a layer down with the ↓ control', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('layer-down-0'))
    const next = onChange.mock.calls[0][0] as Board
    // F.Cu and core swapped.
    expect(next.stackup?.layers[0].name).toBe('core')
    expect(next.stackup?.layers[1].name).toBe('F.Cu')
  })

  it('moves a layer up with the ↑ control', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('layer-up-2'))
    const next = onChange.mock.calls[0][0] as Board
    expect(next.stackup?.layers[1].name).toBe('B.Cu')
    expect(next.stackup?.layers[2].name).toBe('core')
  })

  it('disables ↑ on the first row and ↓ on the last', () => {
    render(<StackupForm board={makeBoard()} onChange={() => {}} />)
    expect(screen.getByLabelText('layer-up-0')).toBeDisabled()
    expect(screen.getByLabelText('layer-down-2')).toBeDisabled()
  })

  it('adds a new copper layer at the end', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '+ Add layer' }))
    const next = onChange.mock.calls[0][0] as Board
    expect(next.stackup?.layers).toHaveLength(4)
    expect(next.stackup?.layers[3]).toMatchObject({
      name: '',
      kind: 'copper',
    })
  })

  it('removes the targeted layer', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('layer-remove-1'))
    const next = onChange.mock.calls[0][0] as Board
    expect(next.stackup?.layers).toHaveLength(2)
    expect(next.stackup?.layers.map((l) => l.name)).toEqual(['F.Cu', 'B.Cu'])
  })

  it('updates power_plane_layers from comma-separated input', () => {
    const onChange = vi.fn()
    render(<StackupForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('power-plane-layers'), {
      target: { value: 'In1.Cu, In2.Cu' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).stackup?.power_plane_layers,
    ).toEqual(['In1.Cu', 'In2.Cu'])
  })
})
