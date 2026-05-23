import { useCallback, useEffect, useRef, useState } from 'react'
import { type BuildResponse, type StageResult, streamBuild } from '../api/client'
import { DoctorBadge } from './DoctorBadge'

type RunState = 'idle' | 'running' | 'done' | 'error'

/** Classify a stage for display: a clean pass, an intentional skip, or a fail. */
function stageStatus(stage: StageResult): 'ok' | 'skip' | 'fail' {
  if (stage.detail.skipped === true) return 'skip'
  return stage.ok ? 'ok' : 'fail'
}

interface PipelinePanelProps {
  /** Called with the build result when a run completes (for the results panes). */
  onResult?: (result: BuildResponse) => void
}

/**
 * Runs the pipeline over the SSE stream and renders each stage as it lands,
 * plus an overall result banner and the environment-health badge.
 */
export function PipelinePanel({ onResult }: PipelinePanelProps) {
  const [stages, setStages] = useState<StageResult[]>([])
  const [runState, setRunState] = useState<RunState>('idle')
  const [result, setResult] = useState<BuildResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<(() => void) | null>(null)

  // Abort an in-flight stream if the panel unmounts.
  useEffect(() => () => abortRef.current?.(), [])

  const runBuild = useCallback(() => {
    abortRef.current?.()
    setStages([])
    setResult(null)
    setError(null)
    setRunState('running')
    abortRef.current = streamBuild(false, {
      onStage: (stage) => setStages((prev) => [...prev, stage]),
      onDone: (res) => {
        setResult(res)
        setRunState('done')
        onResult?.(res)
      },
      onError: (message) => {
        setError(message)
        setRunState('error')
      },
    })
  }, [onResult])

  return (
    <div className="pipeline">
      <div className="pipeline__bar">
        <button
          type="button"
          className="pipeline__run"
          onClick={runBuild}
          disabled={runState === 'running'}
        >
          {runState === 'running' ? 'Building…' : 'Build'}
        </button>
        <DoctorBadge />
      </div>

      {stages.length === 0 && runState === 'idle' && (
        <p className="pane__placeholder">Run a build to see pipeline stages.</p>
      )}

      <ol className="pipeline__stages">
        {stages.map((stage) => {
          const status = stageStatus(stage)
          return (
            <li key={stage.name} className={`stage stage--${status}`}>
              <span className="stage__name">{stage.name}</span>
              <span className="stage__status">{status}</span>
              {status === 'fail' && (
                <pre className="stage__detail">
                  {JSON.stringify(stage.detail, null, 1)}
                </pre>
              )}
            </li>
          )
        })}
      </ol>

      {runState === 'done' && result && (
        <div
          className={`pipeline__result pipeline__result--${
            result.ok ? 'ok' : 'fail'
          }`}
        >
          Build {result.ok ? 'succeeded' : 'failed'}.
        </div>
      )}
      {runState === 'error' && error && (
        <div className="pipeline__result pipeline__result--fail">
          Build error: {error}
        </div>
      )}
    </div>
  )
}
