import { useEffect, useState } from 'react'
import { type Artifact, artifactUrl, getArtifacts } from '../api/client'

// React needs to know about the custom elements KiCanvas exposes so it
// doesn't strip unknown-element attributes.
declare module 'react' {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      'kicanvas-embed': React.DetailedHTMLProps<
        React.HTMLAttributes<HTMLElement> & {
          src?: string
          controls?: string
        },
        HTMLElement
      >
      'kicanvas-source': React.DetailedHTMLProps<
        React.HTMLAttributes<HTMLElement> & { src?: string },
        HTMLElement
      >
    }
  }
}

/** URL of the CDN-hosted KiCanvas script the static viewer also uses. */
const KICANVAS_SCRIPT_URL = 'https://kicanvas.org/kicanvas/kicanvas.js'

/** Marks the script tag so we never inject it more than once per page. */
const KICANVAS_SCRIPT_MARKER = 'data-kicanvas-loader'

interface KiCanvasPreviewProps {
  /** Bump on artifact list change (e.g. after a build) to re-resolve the PCB. */
  refreshKey: number
}

type ArtifactState =
  | { kind: 'loading' }
  | { kind: 'ready'; pcb: Artifact; pro: Artifact | null }
  | { kind: 'no-pcb' }
  | { kind: 'error'; detail: string }

type ScriptState = 'idle' | 'loading' | 'loaded' | 'error'

/** Read the current load state from a pre-existing script tag, if any. */
function detectScriptState(): ScriptState {
  if (typeof document === 'undefined') return 'idle'
  const existing = document.querySelector(`script[${KICANVAS_SCRIPT_MARKER}]`)
  if (existing === null) return 'idle'
  const value = existing.getAttribute(KICANVAS_SCRIPT_MARKER)
  if (value === 'loaded') return 'loaded'
  if (value === 'error') return 'error'
  return 'loading'
}

/**
 * Embedded KiCad PCB preview (SPEC-1 G4 — KiCanvas).
 *
 * Auto-discovers the populated ``.kicad_pcb`` from ``/api/artifacts`` and
 * loads the CDN-hosted KiCanvas script once per page. When a matching
 * ``.kicad_pro`` exists, the embed uses ``<kicanvas-source>`` for the
 * richer project view; otherwise the bare ``src=...kicad_pcb`` form
 * (verified working in the legacy static viewer). On a script-load
 * failure (offline, blocked CDN) the user sees a clear notice — the
 * rest of the GUI keeps working.
 */
export function KiCanvasPreview({ refreshKey }: KiCanvasPreviewProps) {
  const [state, setState] = useState<ArtifactState>({ kind: 'loading' })
  // Lazy initial value reads any pre-existing script tag so a remount
  // inherits the previous load status without a setState-in-effect.
  const [script, setScript] = useState<ScriptState>(detectScriptState)

  useEffect(() => {
    let cancelled = false
    getArtifacts()
      .then((items) => {
        if (cancelled) return
        const pcb = items.find((a) => a.name.endsWith('.kicad_pcb')) ?? null
        const pro = items.find((a) => a.name.endsWith('.kicad_pro')) ?? null
        if (pcb === null) {
          setState({ kind: 'no-pcb' })
        } else {
          setState({ kind: 'ready', pcb, pro })
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setState({
          kind: 'error',
          detail: err instanceof Error ? err.message : String(err),
        })
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  // Lazy-load the KiCanvas script the first time we need it. A
  // previously-injected tag is detected by `detectScriptState` at
  // mount; this effect's only job is creating + attaching listeners
  // when no tag exists yet.
  useEffect(() => {
    if (state.kind !== 'ready') return
    const existing = document.querySelector(
      `script[${KICANVAS_SCRIPT_MARKER}]`,
    )
    if (existing !== null) return
    const tag = document.createElement('script')
    tag.type = 'module'
    tag.src = KICANVAS_SCRIPT_URL
    tag.setAttribute(KICANVAS_SCRIPT_MARKER, 'loading')
    tag.addEventListener('load', () => {
      tag.setAttribute(KICANVAS_SCRIPT_MARKER, 'loaded')
      setScript('loaded')
    })
    tag.addEventListener('error', () => {
      tag.setAttribute(KICANVAS_SCRIPT_MARKER, 'error')
      setScript('error')
    })
    document.head.appendChild(tag)
  }, [state.kind])

  if (state.kind === 'loading') {
    return (
      <div className="results">
        <div className="results__head">PCB preview</div>
        <div className="pane__placeholder">Looking for a .kicad_pcb…</div>
      </div>
    )
  }
  if (state.kind === 'no-pcb') {
    return (
      <div className="results">
        <div className="results__head">PCB preview</div>
        <div className="pane__placeholder">
          Run a build to populate the PCB.
        </div>
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="results">
        <div className="results__head">PCB preview</div>
        <div className="results__error">{state.detail}</div>
      </div>
    )
  }

  if (script === 'error') {
    return (
      <div className="results">
        <div className="results__head">PCB preview</div>
        <div className="results__error">
          PCB preview needs network access — kicanvas.js failed to load
          from {KICANVAS_SCRIPT_URL}.
        </div>
      </div>
    )
  }

  const pcbUrl = artifactUrl(state.pcb.path)
  const proUrl = state.pro !== null ? artifactUrl(state.pro.path) : null

  return (
    <div className="results">
      <div className="results__head">
        PCB preview — <code>{state.pcb.name}</code>
      </div>
      <div className="kicanvas-host" data-script-state={script}>
        {proUrl !== null ? (
          // With a .kicad_pro present, KiCanvas loads the whole project
          // for the richer view (footprint libs + stackup colours).
          <kicanvas-embed controls="full">
            <kicanvas-source src={proUrl} />
          </kicanvas-embed>
        ) : (
          <kicanvas-embed src={pcbUrl} controls="full" />
        )}
      </div>
    </div>
  )
}
