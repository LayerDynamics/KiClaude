/**
 * Integration smoke for G3-T8: with the API mocked to a known working
 * CIR, the App mounts every G3-introduced pane (BOM, impedance,
 * decoupling, return-path, diff) alongside the G1/G2 panes.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  CirState,
  DecouplingCheckResponse,
  DoctorCheck,
  ImpedanceResponse,
  ReturnPathCheckResponse,
} from '../api/client'
import type { AgentClient } from '../api/agent'
import { connectAgent } from '../api/agent'
import {
  decouplingCheck,
  getArtifacts,
  getCir,
  getDoctor,
  impedanceCheck,
  returnPathCheck,
} from '../api/client'
import App from '../App'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>(
    '../api/client',
  )
  return {
    ...actual,
    getCir: vi.fn(),
    getDoctor: vi.fn(),
    getArtifacts: vi.fn(),
    impedanceCheck: vi.fn(),
    decouplingCheck: vi.fn(),
    returnPathCheck: vi.fn(),
  }
})

vi.mock('../api/agent', () => ({
  connectAgent: vi.fn(),
  agentSocketUrl: () => 'ws://test/api/agent',
}))

const mockGetCir = vi.mocked(getCir)
const mockGetDoctor = vi.mocked(getDoctor)
const mockGetArtifacts = vi.mocked(getArtifacts)
const mockImpedance = vi.mocked(impedanceCheck)
const mockDecoupling = vi.mocked(decouplingCheck)
const mockReturnPath = vi.mocked(returnPathCheck)
const mockConnect = vi.mocked(connectAgent)

const FAKE_CIR: CirState = {
  exists: true,
  text: 'name: demo\n',
  parse_error: null,
  board: {
    cir_version: '0.4',
    name: 'demo',
    description: null,
    components: [],
    nets: [],
    constraints: [],
  },
  validation: { ok: true, errors: 0, warnings: 0, issues: [] },
  bom: [
    {
      designator: 'C1',
      comment: '100nF',
      footprint: 'Capacitor_SMD:C_0402_1005Metric',
      mpn: 'CL05B104KO5NNNC',
      lcsc: 'C1525',
      quantity: 1,
    },
  ],
  sourcing: [],
}

const IMPEDANCE: ImpedanceResponse = {
  rows: [
    {
      net: 'USB_DP',
      target_ohm: 90,
      achieved_ohm: 89,
      trace_width_mm: 0.18,
      trace_spacing_mm: 0.13,
      cpwg_gap_mm: null,
      diff_pair_with: 'USB_DM',
    },
  ],
}

const DECOUPLING: DecouplingCheckResponse = {
  ok: true,
  issues: [],
  ics_with_decoupling_declared: ['U1'],
}

const RETURN_PATH: ReturnPathCheckResponse = {
  ok: true,
  issues: [],
  high_speed_nets: [
    { net: 'USB_DP', net_class: 'differential', reference_plane: 'In1.Cu' },
  ],
}

const DOCTOR: DoctorCheck[] = [{ name: 'kicad-cli', ok: true, detail: 'found' }]

beforeEach(() => {
  vi.clearAllMocks()
  mockGetCir.mockResolvedValue(FAKE_CIR)
  mockGetDoctor.mockResolvedValue(DOCTOR)
  mockGetArtifacts.mockResolvedValue([])
  mockImpedance.mockResolvedValue(IMPEDANCE)
  mockDecoupling.mockResolvedValue(DECOUPLING)
  mockReturnPath.mockResolvedValue(RETURN_PATH)
  // The agent panel attempts to connect; give it a no-op client.
  const noopClient: AgentClient = {
    sendPrompt: vi.fn(),
    sendApproval: vi.fn(),
    close: vi.fn(),
  }
  mockConnect.mockReturnValue(noopClient)
})

describe('App — G3 integration', () => {
  it('mounts BOM, impedance, decoupling, return-path and diff panes', async () => {
    await act(async () => {
      render(<App />)
    })

    // Working CIR loads → editor pane shows BOM.
    await waitFor(() =>
      expect(screen.getByText(/BOM — 1 line\(s\)/)).toBeInTheDocument(),
    )

    // The three check panes resolve.
    await waitFor(() => {
      expect(screen.getByText(/Impedance — 1 net/)).toBeInTheDocument()
      expect(screen.getByText(/Decoupling/)).toBeInTheDocument()
      expect(screen.getByText(/Return path/)).toBeInTheDocument()
    })
    // USB_DP shows in both the impedance and return-path tables.
    expect(screen.getAllByText('USB_DP')).toHaveLength(2)
    expect(screen.getByText(/U1/)).toBeInTheDocument()

    // DiffView is rendered but empty until the user picks a baseline.
    expect(screen.getByLabelText('diff-baseline')).toBeInTheDocument()
    expect(
      screen.getByText(/Pick a baseline CIR/),
    ).toBeInTheDocument()
  })

  it('hides the check panes until a parseable working CIR exists', async () => {
    mockGetCir.mockResolvedValueOnce({
      exists: false,
      text: '',
      parse_error: null,
      board: null,
      validation: null,
      bom: [],
      sourcing: [],
    })

    await act(async () => {
      render(<App />)
    })

    // No mounted check pane = no fetch to those endpoints.
    await waitFor(() => expect(mockGetCir).toHaveBeenCalled())
    expect(mockImpedance).not.toHaveBeenCalled()
    expect(mockDecoupling).not.toHaveBeenCalled()
    expect(mockReturnPath).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('diff-baseline')).not.toBeInTheDocument()
  })
})
