import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Board } from '../../api/client'
import { NetsForm } from '../NetsForm'

function makeBoard(): Board {
  return {
    cir_version: '0.4',
    name: 'demo',
    description: null,
    components: [],
    nets: [
      {
        name: '3V3',
        net_class: 'power',
        members: ['U1.1'],
        power_rail: '3V3',
        cross_partition_ok: false,
      },
      {
        name: 'USB_DP',
        net_class: 'differential',
        members: ['U1.5', 'J1.2'],
        diff_pair_with: 'USB_DM',
        cross_partition_ok: false,
        target_impedance_ohm: 90,
        cpwg_gap_mm: null,
        trace_width_mm: 0.18,
        trace_spacing_mm: 0.13,
        reference_plane: 'In1.Cu',
      },
    ],
    constraints: [],
  }
}

describe('NetsForm', () => {
  it('renders one row per net with name + class populated', () => {
    render(<NetsForm board={makeBoard()} onChange={() => {}} />)
    expect(screen.getByLabelText('net-name-0')).toHaveValue('3V3')
    expect(screen.getByLabelText('net-class-0')).toHaveValue('power')
    expect(screen.getByLabelText('net-name-1')).toHaveValue('USB_DP')
    expect(screen.getByLabelText('net-class-1')).toHaveValue('differential')
  })

  it('joins members as comma-separated text', () => {
    render(<NetsForm board={makeBoard()} onChange={() => {}} />)
    expect(screen.getByLabelText('net-members-1')).toHaveValue('U1.5, J1.2')
  })

  it('emits an updated board when the net name changes', () => {
    const onChange = vi.fn()
    render(<NetsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('net-name-0'), {
      target: { value: 'VBUS' },
    })
    expect((onChange.mock.calls[0][0] as Board).nets?.[0].name).toBe('VBUS')
  })

  it('changes net_class through the enum select', () => {
    const onChange = vi.fn()
    render(<NetsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('net-class-0'), {
      target: { value: 'ground' },
    })
    expect((onChange.mock.calls[0][0] as Board).nets?.[0].net_class).toBe(
      'ground',
    )
  })

  it('parses target_impedance_ohm as a number and clears it on empty input', () => {
    const onChange = vi.fn()
    render(<NetsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('net-zo-1'), {
      target: { value: '50' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).nets?.[1].target_impedance_ohm,
    ).toBe(50)

    onChange.mockReset()
    fireEvent.change(screen.getByLabelText('net-zo-1'), { target: { value: '' } })
    expect(
      (onChange.mock.calls[0][0] as Board).nets?.[1].target_impedance_ohm,
    ).toBeNull()
  })

  it('updates fly_by_order from comma-separated input', () => {
    const onChange = vi.fn()
    render(<NetsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('net-fly-by-order-0'), {
      target: { value: 'U_MCU, U_RAM, U_TERM' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).nets?.[0].fly_by_order,
    ).toEqual(['U_MCU', 'U_RAM', 'U_TERM'])
  })

  it('selects a fly-by topology and clears it back to null', () => {
    const onChange = vi.fn()
    render(<NetsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('net-topology-0'), {
      target: { value: 'fly_by' },
    })
    expect((onChange.mock.calls[0][0] as Board).nets?.[0].topology).toBe(
      'fly_by',
    )

    onChange.mockReset()
    fireEvent.change(screen.getByLabelText('net-topology-0'), {
      target: { value: '' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).nets?.[0].topology,
    ).toBeNull()
  })

  it('adds a new net with sensible defaults', () => {
    const onChange = vi.fn()
    render(<NetsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '+ Add net' }))
    const next = onChange.mock.calls[0][0] as Board
    expect(next.nets).toHaveLength(3)
    expect(next.nets?.[2]).toMatchObject({
      name: '',
      net_class: 'signal',
      members: [],
    })
  })

  it('removes the targeted net row', () => {
    const onChange = vi.fn()
    render(<NetsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('net-remove-1'))
    const next = onChange.mock.calls[0][0] as Board
    expect(next.nets).toHaveLength(1)
    expect(next.nets?.[0].name).toBe('3V3')
  })
})
