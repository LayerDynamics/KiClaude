import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type {
  Artifact,
  BuildResponse,
  ValidationSummary,
} from '../../api/client'
import { getArtifacts } from '../../api/client'
import { ArtifactList } from '../ArtifactList'
import { DrcErcView } from '../DrcErcView'
import { SourcingView } from '../SourcingView'
import { ValidationView } from '../ValidationView'

vi.mock('../../api/client', () => ({
  getArtifacts: vi.fn(),
  artifactUrl: (path: string) => `/api/artifacts/${path}`,
}))

const mockGetArtifacts = vi.mocked(getArtifacts)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ValidationView', () => {
  it('shows a parse error', () => {
    render(<ValidationView validation={null} parseError="bad yaml at line 3" />)
    expect(screen.getByText('Parse error')).toBeInTheDocument()
    expect(screen.getByText(/bad yaml at line 3/)).toBeInTheDocument()
  })

  it('reports a clean validation', () => {
    const validation: ValidationSummary = {
      ok: true,
      errors: 0,
      warnings: 0,
      issues: [],
    }
    render(<ValidationView validation={validation} parseError={null} />)
    expect(screen.getByText(/Validation — clean/)).toBeInTheDocument()
  })

  it('lists each validation issue with its code', () => {
    const validation: ValidationSummary = {
      ok: false,
      errors: 1,
      warnings: 0,
      issues: [
        {
          severity: 'error',
          code: 'CIR002',
          message: 'dangling net reference',
          where: 'GND',
        },
      ],
    }
    render(<ValidationView validation={validation} parseError={null} />)
    expect(screen.getByText('CIR002')).toBeInTheDocument()
    expect(screen.getByText('dangling net reference')).toBeInTheDocument()
  })
})

describe('SourcingView', () => {
  it('renders nothing when there are no parts', () => {
    const { container } = render(<SourcingView sourcing={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders a row per component', () => {
    render(
      <SourcingView
        sourcing={[
          { refdes: 'U1', mpn: 'ESP32', status: 'registry_only', lcsc: null },
        ]}
      />,
    )
    expect(screen.getByText('U1')).toBeInTheDocument()
    expect(screen.getByText('registry_only')).toBeInTheDocument()
  })
})

describe('DrcErcView', () => {
  it('renders nothing without a build', () => {
    const { container } = render(<DrcErcView build={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the ERC and DRC stage outcomes', () => {
    const build: BuildResponse = {
      ok: true,
      out_dir: '/tmp/build',
      stages: [
        { name: 'erc', ok: true, detail: { skipped: true, reason: 'no kicad-cli' } },
        { name: 'drc', ok: true, detail: { errors: 0, warnings: 2 } },
      ],
    }
    render(<DrcErcView build={build} />)
    expect(screen.getByText('ERC')).toBeInTheDocument()
    expect(screen.getByText(/no kicad-cli/)).toBeInTheDocument()
    expect(screen.getByText('DRC')).toBeInTheDocument()
    expect(screen.getByText(/2 warning/)).toBeInTheDocument()
  })
})

describe('ArtifactList', () => {
  it('lists fetched artifacts as download links', async () => {
    const artifacts: Artifact[] = [
      { path: 'board.kicad_pcb', name: 'board.kicad_pcb', size: 42 },
    ]
    mockGetArtifacts.mockResolvedValue(artifacts)

    render(<ArtifactList refreshKey={0} />)

    const link = await screen.findByRole('link', { name: 'board.kicad_pcb' })
    expect(link).toHaveAttribute('href', '/api/artifacts/board.kicad_pcb')
  })
})
