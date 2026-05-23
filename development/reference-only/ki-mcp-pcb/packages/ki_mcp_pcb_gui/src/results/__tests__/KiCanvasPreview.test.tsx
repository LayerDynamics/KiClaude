import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Artifact } from '../../api/client'
import { getArtifacts } from '../../api/client'
import { KiCanvasPreview } from '../KiCanvasPreview'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>(
    '../../api/client',
  )
  return { ...actual, getArtifacts: vi.fn() }
})

const mockGetArtifacts = vi.mocked(getArtifacts)

const SCRIPT_SELECTOR = 'script[data-kicanvas-loader]'

function pcb(): Artifact {
  return { path: 'demo.kicad_pcb', name: 'demo.kicad_pcb', size: 1024 }
}
function pro(): Artifact {
  return { path: 'demo.kicad_pro', name: 'demo.kicad_pro', size: 256 }
}

beforeEach(() => {
  mockGetArtifacts.mockReset()
  // Remove any tag from a previous test so the lazy-load path is fresh.
  document.querySelectorAll(SCRIPT_SELECTOR).forEach((node) => node.remove())
})

afterEach(() => {
  document.querySelectorAll(SCRIPT_SELECTOR).forEach((node) => node.remove())
})

describe('KiCanvasPreview', () => {
  it('shows a placeholder when no .kicad_pcb has been built yet', async () => {
    mockGetArtifacts.mockResolvedValueOnce([
      { path: 'notes.txt', name: 'notes.txt', size: 4 },
    ])
    render(<KiCanvasPreview refreshKey={0} />)
    expect(
      await screen.findByText(/Run a build to populate the PCB/),
    ).toBeInTheDocument()
    // No script tag was added — there's nothing to render.
    expect(document.querySelector(SCRIPT_SELECTOR)).toBeNull()
  })

  it('embeds the bare .kicad_pcb url when no .kicad_pro is present', async () => {
    mockGetArtifacts.mockResolvedValueOnce([pcb()])
    const { container } = render(<KiCanvasPreview refreshKey={0} />)

    const embed = await waitFor(() => {
      const found = container.querySelector('kicanvas-embed')
      if (!found) throw new Error('embed not yet rendered')
      return found
    })
    expect(embed.getAttribute('src')).toBe('/api/artifacts/demo.kicad_pcb')
    expect(embed.getAttribute('controls')).toBe('full')
    expect(container.querySelector('kicanvas-source')).toBeNull()
    // Script tag injected exactly once.
    expect(document.querySelectorAll(SCRIPT_SELECTOR)).toHaveLength(1)
  })

  it('uses <kicanvas-source> when the matching .kicad_pro exists', async () => {
    mockGetArtifacts.mockResolvedValueOnce([pcb(), pro()])
    const { container } = render(<KiCanvasPreview refreshKey={0} />)

    const source = await waitFor(() => {
      const found = container.querySelector('kicanvas-source')
      if (!found) throw new Error('source not yet rendered')
      return found
    })
    expect(source.getAttribute('src')).toBe('/api/artifacts/demo.kicad_pro')
    // With a project source, the embed itself has no src.
    const embed = container.querySelector('kicanvas-embed')
    expect(embed?.getAttribute('src')).toBeNull()
  })

  it('shows an offline-degrade notice when the CDN script errors', async () => {
    mockGetArtifacts.mockResolvedValueOnce([pcb()])
    render(<KiCanvasPreview refreshKey={0} />)

    const tag = await waitFor(() => {
      const node = document.querySelector(SCRIPT_SELECTOR) as HTMLScriptElement | null
      if (!node) throw new Error('script not injected yet')
      return node
    })
    tag.dispatchEvent(new Event('error'))

    expect(
      await screen.findByText(/PCB preview needs network access/),
    ).toBeInTheDocument()
  })

  it('flips to "loaded" once the script load event fires', async () => {
    mockGetArtifacts.mockResolvedValueOnce([pcb()])
    const { container } = render(<KiCanvasPreview refreshKey={0} />)

    const tag = await waitFor(() => {
      const node = document.querySelector(SCRIPT_SELECTOR) as HTMLScriptElement | null
      if (!node) throw new Error('script not injected yet')
      return node
    })
    tag.dispatchEvent(new Event('load'))

    await waitFor(() => {
      const host = container.querySelector('.kicanvas-host')
      expect(host?.getAttribute('data-script-state')).toBe('loaded')
    })
  })

  it('reuses a previously-loaded script tag without injecting another', async () => {
    // Simulate a tag already in the document from an earlier mount.
    const existing = document.createElement('script')
    existing.setAttribute('data-kicanvas-loader', 'loaded')
    existing.type = 'module'
    document.head.appendChild(existing)

    mockGetArtifacts.mockResolvedValueOnce([pcb()])
    const { container } = render(<KiCanvasPreview refreshKey={0} />)

    await waitFor(() => {
      const host = container.querySelector('.kicanvas-host')
      expect(host?.getAttribute('data-script-state')).toBe('loaded')
    })
    // No second tag was injected.
    expect(document.querySelectorAll(SCRIPT_SELECTOR)).toHaveLength(1)
  })

  it('refetches when refreshKey changes', async () => {
    mockGetArtifacts.mockResolvedValue([pcb()])
    const { rerender, container } = render(<KiCanvasPreview refreshKey={0} />)
    await waitFor(() =>
      expect(container.querySelector('kicanvas-embed')).not.toBeNull(),
    )
    expect(mockGetArtifacts).toHaveBeenCalledTimes(1)

    rerender(<KiCanvasPreview refreshKey={1} />)
    await waitFor(() => expect(mockGetArtifacts).toHaveBeenCalledTimes(2))
  })
})
