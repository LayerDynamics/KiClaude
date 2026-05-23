/**
 * Integration smoke for G4-T7: with the API mocked to a known working
 * CIR + workspace, the App mounts every G4-introduced surface alongside
 * the G1/G2/G3 panes — workspace control in the header, intent dialog
 * launcher in the editor pane, sign-off in the editor pane (only when
 * a board exists), and KiCanvas in the center pane.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { connectAgent } from '../api/agent'
import type { AgentClient } from '../api/agent'
import type {
  CirState,
  DecouplingCheckResponse,
  DoctorCheck,
  ImpedanceResponse,
  ParseIntentResponse,
  ReturnPathCheckResponse,
  WorkspaceState,
} from '../api/client'
import {
  decouplingCheck,
  getArtifacts,
  getCir,
  getDoctor,
  getWorkspace,
  impedanceCheck,
  parseIntent,
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
    getWorkspace: vi.fn(),
    parseIntent: vi.fn(),
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
const mockGetWorkspace = vi.mocked(getWorkspace)
const mockParseIntent = vi.mocked(parseIntent)
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
    signoff: {
      rf_reviewed: false,
      ddr_reviewed: false,
      bga_fanout_reviewed: false,
      reviewer: null,
      reviewed_at: null,
    },
  },
  validation: { ok: true, errors: 0, warnings: 0, issues: [] },
  bom: [],
  sourcing: [],
}

const EMPTY_CIR: CirState = {
  exists: false,
  text: '',
  parse_error: null,
  board: null,
  validation: null,
  bom: [],
  sourcing: [],
}

const WORKSPACE: WorkspaceState = {
  path: '/work/demo',
  source: 'persisted',
}

const IMPEDANCE: ImpedanceResponse = { rows: [] }
const DECOUPLING: DecouplingCheckResponse = {
  ok: true,
  issues: [],
  ics_with_decoupling_declared: [],
}
const RETURN_PATH: ReturnPathCheckResponse = {
  ok: true,
  issues: [],
  high_speed_nets: [],
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
  mockGetWorkspace.mockResolvedValue(WORKSPACE)
  const noopClient: AgentClient = {
    sendPrompt: vi.fn(),
    sendApproval: vi.fn(),
    close: vi.fn(),
  }
  mockConnect.mockReturnValue(noopClient)
  // Clean any leftover kicanvas script from previous tests.
  document
    .querySelectorAll('script[data-kicanvas-loader]')
    .forEach((node) => node.remove())
})

describe('App — G4 integration', () => {
  it('mounts the workspace control, signoff and KiCanvas alongside G1-G3', async () => {
    await act(async () => {
      render(<App />)
    })

    // WorkspacePanel in the header surfaced the persisted path + label.
    expect(
      await screen.findByDisplayValue('/work/demo'),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/remembered from last session/),
    ).toBeInTheDocument()

    // SignoffPanel in the editor pane.
    await waitFor(() =>
      expect(screen.getByText('Sign-off')).toBeInTheDocument(),
    )
    expect(screen.getByLabelText('signoff-rf')).toBeInTheDocument()
    expect(screen.getByLabelText('signoff-ddr')).toBeInTheDocument()
    expect(screen.getByLabelText('signoff-bga')).toBeInTheDocument()

    // KiCanvas pane mounted; no .kicad_pcb in artifacts ⇒ placeholder.
    expect(
      screen.getByText(/Run a build to populate the PCB/),
    ).toBeInTheDocument()

    // The intent launcher is always visible.
    expect(
      screen.getByRole('button', { name: 'New from intent…' }),
    ).toBeInTheDocument()
  })

  it('opens the IntentDialog when the launcher is clicked', async () => {
    await act(async () => {
      render(<App />)
    })
    await screen.findByDisplayValue('/work/demo')

    fireEvent.click(screen.getByRole('button', { name: 'New from intent…' }))

    expect(screen.getByLabelText('intent-prompt')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Generate' }),
    ).toBeInTheDocument()
  })

  it('hides the signoff and check panes until a working CIR exists', async () => {
    mockGetCir.mockResolvedValueOnce(EMPTY_CIR)
    await act(async () => {
      render(<App />)
    })
    // Editor empty-state hint appears in *both* the dedicated empty
    // banner and the CirEditor status line — both surfaces tell the
    // user the same thing on a fresh launch.
    const hints = await screen.findAllByText(/No working CIR yet/)
    expect(hints.length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('Sign-off')).not.toBeInTheDocument()
    // Check endpoints were never called.
    expect(mockImpedance).not.toHaveBeenCalled()
    expect(mockDecoupling).not.toHaveBeenCalled()
    expect(mockReturnPath).not.toHaveBeenCalled()
  })

  it('completes the intent → accept → form-unlock end-to-end (T8 milestone exit)', async () => {
    // Boot with no CIR; the form tab must start disabled.
    mockGetCir.mockResolvedValueOnce(EMPTY_CIR)
    const draft: ParseIntentResponse = {
      board: {
        cir_version: '0.4',
        name: 'fresh',
        description: null,
        components: [],
        nets: [],
        constraints: [],
      },
      draft_yaml: 'cir_version: "0.4"\nname: fresh\n',
    }
    mockParseIntent.mockResolvedValueOnce(draft)
    // After Accept the writer flush() calls /api/cir; the next /api/cir
    // GET (from CirEditor's reload) returns the new board so the Form
    // tab unlocks.
    mockGetCir.mockResolvedValue({ ...FAKE_CIR, text: draft.draft_yaml })

    await act(async () => {
      render(<App />)
    })

    // Form tab is disabled on empty CIR.
    const formTab = await screen.findByRole('tab', { name: 'Form' })
    expect(formTab).toBeDisabled()

    // Open the dialog and Generate.
    fireEvent.click(screen.getByRole('button', { name: 'New from intent…' }))
    fireEvent.change(screen.getByLabelText('intent-prompt'), {
      target: { value: 'an ESP32-S3 dev board' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))

    // Preview appears, then Accept.
    await screen.findByLabelText('intent-draft-yaml')
    fireEvent.click(screen.getByRole('button', { name: 'Accept as working CIR' }))

    // After the writer flushes + cirReload bumps, the next CirEditor
    // load returns the new board → Form tab enables.
    await waitFor(() =>
      expect(
        screen.getByRole('tab', { name: 'Form' }),
      ).not.toBeDisabled(),
    )
  })
})
