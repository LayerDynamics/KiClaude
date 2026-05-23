import { useCallback, useState } from 'react'
import './App.css'
import type { Board, BuildResponse, CirState, Signoff } from './api/client'
import { ChatPanel } from './chat/ChatPanel'
import { CirEditor } from './cir/CirEditor'
import { useCirWriter } from './cir/useCirWriter'
import { BoardForm } from './form/BoardForm'
import { IntentDialog } from './intent/IntentDialog'
import { PipelinePanel } from './pipeline/PipelinePanel'
import { ArtifactList } from './results/ArtifactList'
import { BomView } from './results/BomView'
import { DecouplingView } from './results/DecouplingView'
import { DiffView } from './results/DiffView'
import { DrcErcView } from './results/DrcErcView'
import { ImpedanceView } from './results/ImpedanceView'
import { KiCanvasPreview } from './results/KiCanvasPreview'
import { ReturnPathView } from './results/ReturnPathView'
import { SourcingView } from './results/SourcingView'
import { ValidationView } from './results/ValidationView'
import { SignoffPanel } from './signoff/SignoffPanel'
import { WorkspacePanel } from './workspace/WorkspacePanel'

type EditorMode = 'text' | 'form'

/**
 * The ki-mcp-pcb GUI co-pilot shell (SPEC-1 §6.6).
 *
 * Three-pane window: CIR editor (text + structured form) with
 * validation/sourcing/BOM and human-only sign-off; pipeline & build
 * results plus the KiCanvas board preview; the Claude co-pilot chat.
 * Text and form modes write through a single-flight ``useCirWriter`` so
 * neither can clobber the other (SPEC-1 G3-T6 FR-4). The header carries
 * the persisted workspace control (G4). The intent-to-CIR dialog
 * (FR-5) opens from the editor empty state.
 */
function App() {
  const [cirState, setCirState] = useState<CirState | null>(null)
  const [buildResult, setBuildResult] = useState<BuildResponse | null>(null)
  const [artifactRefresh, setArtifactRefresh] = useState(0)
  const [cirReload, setCirReload] = useState(0)
  const [editorMode, setEditorMode] = useState<EditorMode>('text')
  const [intentOpen, setIntentOpen] = useState(false)

  const handleCirState = useCallback((state: CirState) => {
    setCirState(state)
  }, [])

  const writer = useCirWriter({ onState: handleCirState })

  const handleBuildResult = useCallback((result: BuildResponse) => {
    setBuildResult(result)
    setArtifactRefresh((count) => count + 1)
  }, [])

  // Either the co-pilot, the form editor, the intent flow, or a sign-off
  // PATCH wrote a new CIR — reload the text editor so the textarea
  // reflects the on-disk canonical YAML (and via its `onState` callback
  // the validation/sourcing panes refresh).
  const handleCirChanged = useCallback(() => {
    setCirReload((count) => count + 1)
  }, [])

  const board: Board | null = (cirState?.board ?? null) as Board | null
  const signoff: Signoff | null =
    board !== null && board.signoff !== undefined
      ? (board.signoff as Signoff)
      : null
  const hasWorkingCir = cirState?.exists === true && board !== null

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">ki-mcp-pcb</h1>
        <span className="app__tagline">text → PCB co-pilot</span>
        <div className="app__header-spacer" />
        <WorkspacePanel />
      </header>
      <main className="app__panes">
        <section className="pane pane--editor" aria-label="CIR editor">
          <div className="editor-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={editorMode === 'text'}
              className={`editor-tab${editorMode === 'text' ? ' editor-tab--active' : ''}`}
              onClick={() => setEditorMode('text')}
            >
              Text
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={editorMode === 'form'}
              className={`editor-tab${editorMode === 'form' ? ' editor-tab--active' : ''}`}
              onClick={() => setEditorMode('form')}
              disabled={board === null}
              title={board === null ? 'Save a parseable CIR first' : undefined}
            >
              Form
            </button>
            <div className="editor-tabs__spacer" />
            <button
              type="button"
              className="editor-intent"
              onClick={() => setIntentOpen(true)}
            >
              New from intent…
            </button>
          </div>
          {!hasWorkingCir && editorMode === 'text' && (
            <div className="editor-empty">
              <p>
                No working CIR yet. Type or paste YAML below — or click
                <button
                  type="button"
                  className="editor-empty__link"
                  onClick={() => setIntentOpen(true)}
                >
                  New from intent
                </button>
                to describe the board in plain English.
              </p>
            </div>
          )}
          {editorMode === 'text' && (
            <CirEditor
              writer={writer}
              onState={handleCirState}
              reloadKey={cirReload}
            />
          )}
          {editorMode === 'form' && board !== null && (
            <BoardForm
              key={cirReload}
              board={board}
              writer={writer}
              onCirChanged={handleCirChanged}
            />
          )}
          <ValidationView
            validation={cirState?.validation ?? null}
            parseError={cirState?.parse_error ?? null}
          />
          <SourcingView sourcing={cirState?.sourcing ?? []} />
          <BomView bom={cirState?.bom ?? []} />
          {signoff !== null && (
            <SignoffPanel
              key={`signoff-${cirReload}`}
              signoff={signoff}
              writer={writer}
              onSignoffChanged={handleCirChanged}
            />
          )}
        </section>
        <section className="pane pane--center" aria-label="Pipeline and results">
          <PipelinePanel onResult={handleBuildResult} />
          <DrcErcView build={buildResult} />
          {/* Design-intent checks that read the working CIR — refresh on
              every write the user or the co-pilot makes. Only mounted once
              there is a parseable working CIR on disk, so the 400 "no
              working CIR" path doesn't surface on a fresh launch. */}
          {hasWorkingCir && (
            <>
              <ImpedanceView refreshKey={cirReload} />
              <DecouplingView refreshKey={cirReload} />
              <ReturnPathView refreshKey={cirReload} />
              <DiffView />
            </>
          )}
          <ArtifactList refreshKey={artifactRefresh} />
          {/* KiCanvas auto-discovers the populated PCB from the artifact
              list, so it only renders something meaningful after a build. */}
          <KiCanvasPreview refreshKey={artifactRefresh} />
        </section>
        <section className="pane pane--chat" aria-label="Claude co-pilot">
          <ChatPanel onCirChanged={handleCirChanged} />
        </section>
      </main>
      <IntentDialog
        writer={writer}
        open={intentOpen}
        onClose={() => setIntentOpen(false)}
        onAccepted={handleCirChanged}
      />
    </div>
  )
}

export default App
