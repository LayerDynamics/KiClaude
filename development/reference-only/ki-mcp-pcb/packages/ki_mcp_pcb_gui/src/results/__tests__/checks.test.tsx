import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  DecouplingCheckResponse,
  DiffResponse,
  ImpedanceResponse,
  ReturnPathCheckResponse,
} from '../../api/client'
import {
  ApiError,
  decouplingCheck,
  diffAgainstWorking,
  impedanceCheck,
  returnPathCheck,
} from '../../api/client'
import { DecouplingView } from '../DecouplingView'
import { DiffView } from '../DiffView'
import { ImpedanceView } from '../ImpedanceView'
import { ReturnPathView } from '../ReturnPathView'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>(
    '../../api/client',
  )
  return {
    ...actual,
    decouplingCheck: vi.fn(),
    returnPathCheck: vi.fn(),
    impedanceCheck: vi.fn(),
    diffAgainstWorking: vi.fn(),
  }
})

const mockDecoupling = vi.mocked(decouplingCheck)
const mockReturnPath = vi.mocked(returnPathCheck)
const mockImpedance = vi.mocked(impedanceCheck)
const mockDiff = vi.mocked(diffAgainstWorking)

beforeEach(() => {
  mockDecoupling.mockReset()
  mockReturnPath.mockReset()
  mockImpedance.mockReset()
  mockDiff.mockReset()
})

// --------------------------------------------------------------------------
// ImpedanceView
// --------------------------------------------------------------------------
describe('ImpedanceView', () => {
  function withRows(rows: ImpedanceResponse['rows']): ImpedanceResponse {
    return { rows }
  }

  it('renders a row per impedance-constrained net', async () => {
    mockImpedance.mockResolvedValueOnce(
      withRows([
        {
          net: 'USB_DP',
          target_ohm: 90,
          achieved_ohm: 91.2,
          trace_width_mm: 0.18,
          trace_spacing_mm: 0.13,
          cpwg_gap_mm: null,
          diff_pair_with: 'USB_DM',
        },
      ]),
    )

    render(<ImpedanceView refreshKey={0} />)
    await waitFor(() =>
      expect(screen.getByText('USB_DP')).toBeInTheDocument(),
    )
    expect(screen.getByText('91.20')).toBeInTheDocument()
    expect(screen.getByText('USB_DM')).toBeInTheDocument()
  })

  it('flags an out-of-tolerance row with the bad severity class', async () => {
    mockImpedance.mockResolvedValueOnce(
      withRows([
        {
          net: 'RF',
          target_ohm: 50,
          achieved_ohm: 80, // 60% off → bad
          trace_width_mm: 0.2,
          trace_spacing_mm: null,
          cpwg_gap_mm: 0.3,
          diff_pair_with: null,
        },
      ]),
    )

    render(<ImpedanceView refreshKey={0} />)
    const row = await screen.findByText('RF')
    expect(row.closest('tr')).toHaveClass('zo-row--bad')
  })

  it('shows a placeholder when no nets declare a target impedance', async () => {
    mockImpedance.mockResolvedValueOnce(withRows([]))
    render(<ImpedanceView refreshKey={0} />)
    expect(
      await screen.findByText(/No nets declare/),
    ).toBeInTheDocument()
  })

  it('refetches when refreshKey changes', async () => {
    mockImpedance.mockResolvedValue(withRows([]))
    const { rerender } = render(<ImpedanceView refreshKey={0} />)
    await waitFor(() => expect(mockImpedance).toHaveBeenCalledOnce())
    rerender(<ImpedanceView refreshKey={1} />)
    await waitFor(() => expect(mockImpedance).toHaveBeenCalledTimes(2))
  })

  it('renders an API error', async () => {
    mockImpedance.mockRejectedValueOnce(new ApiError(400, 'no working CIR'))
    render(<ImpedanceView refreshKey={0} />)
    expect(await screen.findByText('no working CIR')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// DecouplingView
// --------------------------------------------------------------------------
describe('DecouplingView', () => {
  function payload(
    ok: boolean,
    codes: string[],
    ics: string[] = [],
  ): DecouplingCheckResponse {
    return {
      ok,
      issues: codes.map((code) => ({
        code,
        severity: ok ? 'warning' : 'error',
        message: `${code} fired`,
        where: null,
      })),
      ics_with_decoupling_declared: ics,
    }
  }

  it('renders the ok badge + the IC list on a clean board', async () => {
    mockDecoupling.mockResolvedValueOnce(payload(true, [], ['U1', 'U2']))
    render(<DecouplingView refreshKey={0} />)
    expect(await screen.findByText('ok')).toBeInTheDocument()
    expect(screen.getByText(/U1, U2/)).toBeInTheDocument()
  })

  it('renders the fail badge and each CIR030 issue', async () => {
    mockDecoupling.mockResolvedValueOnce(payload(false, ['CIR030'], []))
    render(<DecouplingView refreshKey={0} />)
    expect(await screen.findByText('fail')).toBeInTheDocument()
    expect(screen.getByText('CIR030')).toBeInTheDocument()
    expect(screen.getByText(/CIR030 fired/)).toBeInTheDocument()
    expect(screen.getByText(/none/)).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// ReturnPathView
// --------------------------------------------------------------------------
describe('ReturnPathView', () => {
  function payload(
    ok: boolean,
    hs: ReturnPathCheckResponse['high_speed_nets'],
  ): ReturnPathCheckResponse {
    return {
      ok,
      issues: ok
        ? []
        : [
            {
              code: 'CIR090',
              severity: 'warning',
              message: 'missing plane',
              where: null,
            },
          ],
      high_speed_nets: hs,
    }
  }

  it('lists the high-speed nets and their reference planes', async () => {
    mockReturnPath.mockResolvedValueOnce(
      payload(true, [
        { net: 'USB_DP', net_class: 'differential', reference_plane: 'In1.Cu' },
      ]),
    )
    render(<ReturnPathView refreshKey={0} />)
    expect(await screen.findByText('USB_DP')).toBeInTheDocument()
    expect(screen.getByText('In1.Cu')).toBeInTheDocument()
    expect(screen.getByText('ok')).toBeInTheDocument()
  })

  it('flags warnings when CIR090 fires', async () => {
    mockReturnPath.mockResolvedValueOnce(
      payload(false, [
        { net: 'I2S_BCLK', net_class: 'high_speed', reference_plane: null },
      ]),
    )
    render(<ReturnPathView refreshKey={0} />)
    expect(await screen.findByText('warn')).toBeInTheDocument()
    expect(screen.getByText(/missing plane/)).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// DiffView
// --------------------------------------------------------------------------
describe('DiffView', () => {
  const FILE = new File(['name: before'], 'baseline.yaml', {
    type: 'application/x-yaml',
  })

  function diff(overrides: Partial<DiffResponse> = {}): DiffResponse {
    return {
      identical: false,
      summary: '1 component added, 1 net removed',
      name_changed: ['old', 'new'],
      components_added: ['U2'],
      components_removed: [],
      component_changes: [
        { refdes: 'U1', field: 'mpn', left: 'OLD-MPN', right: 'NEW-MPN' },
      ],
      nets_added: [],
      nets_removed: ['LEGACY'],
      net_changes: [],
      ...overrides,
    }
  }

  function pickBaseline() {
    fireEvent.change(screen.getByLabelText('diff-baseline'), {
      target: { files: [FILE] },
    })
  }

  it('starts empty and prompts for a baseline', () => {
    render(<DiffView />)
    expect(screen.getByText(/Pick a baseline/)).toBeInTheDocument()
  })

  it('renders the structured diff after picking a baseline', async () => {
    mockDiff.mockResolvedValueOnce(diff())
    render(<DiffView />)
    pickBaseline()

    expect(await screen.findByText(/1 component added/)).toBeInTheDocument()
    expect(mockDiff).toHaveBeenCalledOnce()
    // Name change rendered.
    expect(screen.getByText('old')).toBeInTheDocument()
    expect(screen.getByText('new')).toBeInTheDocument()
    // Added/removed lists.
    expect(screen.getByText(/Components added \(1\)/)).toBeInTheDocument()
    expect(screen.getByText('U2')).toBeInTheDocument()
    expect(screen.getByText(/Nets removed \(1\)/)).toBeInTheDocument()
    // Component-changes table row.
    expect(screen.getByText('OLD-MPN')).toBeInTheDocument()
    expect(screen.getByText('NEW-MPN')).toBeInTheDocument()
  })

  it('says "identical" when the diff returns no changes', async () => {
    mockDiff.mockResolvedValueOnce(
      diff({
        identical: true,
        name_changed: null,
        components_added: [],
        components_removed: [],
        component_changes: [],
        nets_added: [],
        nets_removed: [],
        net_changes: [],
      }),
    )
    render(<DiffView />)
    pickBaseline()
    expect(
      await screen.findByText(/identical to/),
    ).toBeInTheDocument()
  })

  it('surfaces a baseline-parse error', async () => {
    mockDiff.mockRejectedValueOnce(new ApiError(400, 'parse error: bad YAML'))
    render(<DiffView />)
    pickBaseline()
    expect(
      await screen.findByText('parse error: bad YAML'),
    ).toBeInTheDocument()
  })
})
