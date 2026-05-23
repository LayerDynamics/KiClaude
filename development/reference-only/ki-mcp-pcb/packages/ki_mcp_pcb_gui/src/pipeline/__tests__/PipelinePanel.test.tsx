import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import type {
  BuildStreamHandlers,
  DoctorCheck,
  StageResult,
} from '../../api/client'
import { getDoctor, streamBuild } from '../../api/client'
import { PipelinePanel } from '../PipelinePanel'

vi.mock('../../api/client', () => ({
  streamBuild: vi.fn(),
  getDoctor: vi.fn(),
}))

const mockStreamBuild = vi.mocked(streamBuild)
const mockGetDoctor = vi.mocked(getDoctor)

function stage(
  name: string,
  ok: boolean,
  detail: Record<string, unknown> = {},
): StageResult {
  return { name, ok, detail }
}

/** Render the panel and return the stream handlers the Build button wired up. */
function renderAndBuild(): BuildStreamHandlers {
  let handlers: BuildStreamHandlers | undefined
  mockStreamBuild.mockImplementation((_runRoute, given) => {
    handlers = given
    return () => {}
  })
  render(<PipelinePanel />)
  fireEvent.click(screen.getByRole('button', { name: 'Build' }))
  if (!handlers) throw new Error('streamBuild was not called')
  return handlers
}

beforeEach(() => {
  vi.clearAllMocks()
  const checks: DoctorCheck[] = [
    { name: 'kicad-cli', ok: true, detail: 'found' },
  ]
  mockGetDoctor.mockResolvedValue(checks)
})

describe('PipelinePanel', () => {
  it('shows the doctor badge once the environment check resolves', async () => {
    mockStreamBuild.mockReturnValue(() => {})
    render(<PipelinePanel />)
    expect(await screen.findByText(/env 1\/1/)).toBeInTheDocument()
  })

  it('streams stages and shows the overall result on Build', () => {
    const handlers = renderAndBuild()
    expect(mockStreamBuild).toHaveBeenCalledOnce()

    act(() => {
      handlers.onStage(stage('parse', true))
      handlers.onStage(stage('drc', true, { skipped: true }))
    })
    expect(screen.getByText('parse')).toBeInTheDocument()
    expect(screen.getByText('drc')).toBeInTheDocument()

    act(() => {
      handlers.onDone({ ok: false, stages: [], out_dir: '/tmp/build' })
    })
    expect(screen.getByText(/Build failed/)).toBeInTheDocument()
  })

  it('renders a failed stage with its detail', () => {
    const handlers = renderAndBuild()
    act(() => {
      handlers.onStage(stage('fab', false, { error: 'zip failed' }))
    })
    expect(screen.getByText('fab')).toBeInTheDocument()
    expect(screen.getByText(/zip failed/)).toBeInTheDocument()
  })

  it('surfaces a build-stream error', () => {
    const handlers = renderAndBuild()
    act(() => {
      handlers.onError('pipeline crashed')
    })
    expect(screen.getByText(/pipeline crashed/)).toBeInTheDocument()
  })
})
