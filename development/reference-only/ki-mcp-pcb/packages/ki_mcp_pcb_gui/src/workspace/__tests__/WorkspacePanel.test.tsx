import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  type WorkspaceState,
  getWorkspace,
  setWorkspace,
} from '../../api/client'
import { WorkspacePanel } from '../WorkspacePanel'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>(
    '../../api/client',
  )
  return {
    ...actual,
    getWorkspace: vi.fn(),
    setWorkspace: vi.fn(),
  }
})

const mockGetWorkspace = vi.mocked(getWorkspace)
const mockSetWorkspace = vi.mocked(setWorkspace)

const ORIGINAL_LOCATION = window.location
const reloadSpy = vi.fn()

beforeEach(() => {
  mockGetWorkspace.mockReset()
  mockSetWorkspace.mockReset()
  reloadSpy.mockReset()
  // jsdom forbids replacing window.location.reload directly; redefine.
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...ORIGINAL_LOCATION, reload: reloadSpy },
  })
})

afterEach(() => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: ORIGINAL_LOCATION,
  })
})

function state(
  overrides: Partial<WorkspaceState> = {},
): WorkspaceState {
  return {
    path: '/tmp/demo',
    source: 'default',
    ...overrides,
  }
}

describe('WorkspacePanel', () => {
  it('renders the current workspace path and source label', async () => {
    mockGetWorkspace.mockResolvedValueOnce(
      state({ path: '/work/board', source: 'persisted' }),
    )
    render(<WorkspacePanel />)
    const input = await screen.findByLabelText('workspace-path')
    await waitFor(() => expect(input).toHaveValue('/work/board'))
    expect(screen.getByText(/remembered from last session/)).toBeInTheDocument()
  })

  it('disables Open while the draft matches the current path', async () => {
    mockGetWorkspace.mockResolvedValueOnce(state({ path: '/same' }))
    render(<WorkspacePanel />)
    await screen.findByDisplayValue('/same')
    expect(screen.getByRole('button', { name: 'Open' })).toBeDisabled()
  })

  it('locks the input when the source is env (KIMP_GUI_WORKDIR override)', async () => {
    mockGetWorkspace.mockResolvedValueOnce(
      state({ path: '/env/dir', source: 'env' }),
    )
    render(<WorkspacePanel />)
    const input = await screen.findByLabelText('workspace-path')
    expect(input).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: 'Open' })).toBeDisabled()
  })

  it('POSTs the new path and reloads on success', async () => {
    mockGetWorkspace.mockResolvedValueOnce(state({ path: '/old' }))
    mockSetWorkspace.mockResolvedValueOnce(
      state({ path: '/new', source: 'persisted' }),
    )

    render(<WorkspacePanel />)
    const input = await screen.findByLabelText('workspace-path')
    await waitFor(() => expect(input).toHaveValue('/old'))

    fireEvent.change(input, { target: { value: '/new' } })
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))

    await waitFor(() =>
      expect(mockSetWorkspace).toHaveBeenCalledWith('/new'),
    )
    await waitFor(() => expect(reloadSpy).toHaveBeenCalledOnce())
  })

  it('surfaces an ApiError without reloading the page', async () => {
    mockGetWorkspace.mockResolvedValueOnce(state({ path: '/old' }))
    mockSetWorkspace.mockRejectedValueOnce(
      new ApiError(400, 'workspace path must be absolute'),
    )

    render(<WorkspacePanel />)
    const input = await screen.findByLabelText('workspace-path')
    fireEvent.change(input, { target: { value: 'relative' } })
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))

    expect(
      await screen.findByText(/workspace path must be absolute/),
    ).toBeInTheDocument()
    expect(reloadSpy).not.toHaveBeenCalled()
  })
})
