import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Board } from '../../api/client'
import { ComponentsForm } from '../ComponentsForm'

function makeBoard(): Board {
  return {
    cir_version: '0.4',
    name: 'demo',
    description: null,
    components: [
      {
        refdes: 'U1',
        mpn: 'ESP32-S3-WROOM-1',
        value: null,
        partition: 'digital',
        decoupling_pins: ['1', '2'],
        bga_pitch_mm: null,
        is_bridge: false,
      },
    ],
    nets: [],
    constraints: [],
  }
}

describe('ComponentsForm', () => {
  it('renders one row per component with refdes/mpn populated', () => {
    render(<ComponentsForm board={makeBoard()} onChange={() => {}} />)
    expect(screen.getByLabelText('refdes-0')).toHaveValue('U1')
    expect(screen.getByLabelText('mpn-0')).toHaveValue('ESP32-S3-WROOM-1')
    expect(screen.getByLabelText('decoupling-pins-0')).toHaveValue('1, 2')
  })

  it('emits an updated board when the refdes is edited', () => {
    const onChange = vi.fn()
    render(<ComponentsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('refdes-0'), {
      target: { value: 'U99' },
    })
    expect(onChange).toHaveBeenCalledOnce()
    const next = onChange.mock.calls[0][0] as Board
    expect(next.components?.[0].refdes).toBe('U99')
    // Other components fields are preserved.
    expect(next.components?.[0].mpn).toBe('ESP32-S3-WROOM-1')
  })

  it('splits comma-separated decoupling pins into a string list', () => {
    const onChange = vi.fn()
    render(<ComponentsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('decoupling-pins-0'), {
      target: { value: '4, 5, 6 ' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).components?.[0].decoupling_pins,
    ).toEqual(['4', '5', '6'])
  })

  it('parses bga_pitch as a number, treating empty as null', () => {
    const onChange = vi.fn()
    render(<ComponentsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('bga-pitch-0'), {
      target: { value: '0.5' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).components?.[0].bga_pitch_mm,
    ).toBe(0.5)
  })

  it('clears a partition selection back to null on the empty option', () => {
    const onChange = vi.fn()
    render(<ComponentsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('partition-0'), {
      target: { value: '' },
    })
    expect(
      (onChange.mock.calls[0][0] as Board).components?.[0].partition,
    ).toBeNull()
  })

  it('adds a new empty row when + Add component is clicked', () => {
    const onChange = vi.fn()
    render(<ComponentsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '+ Add component' }))
    const next = onChange.mock.calls[0][0] as Board
    expect(next.components).toHaveLength(2)
    expect(next.components?.[1]).toMatchObject({
      refdes: '',
      mpn: '',
      is_bridge: false,
    })
  })

  it('removes a row when the × button is clicked', () => {
    const onChange = vi.fn()
    render(<ComponentsForm board={makeBoard()} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('remove-0'))
    expect((onChange.mock.calls[0][0] as Board).components).toHaveLength(0)
  })

  it('shows a placeholder row when the board has no components', () => {
    const empty: Board = { ...makeBoard(), components: [] }
    render(<ComponentsForm board={empty} onChange={() => {}} />)
    expect(screen.getByText(/No components yet/)).toBeInTheDocument()
  })
})
