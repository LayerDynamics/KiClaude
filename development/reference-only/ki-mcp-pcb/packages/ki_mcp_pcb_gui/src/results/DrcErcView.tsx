import type { BuildResponse, StageResult } from '../api/client'

interface DrcErcViewProps {
  build: BuildResponse | null
}

/** Surfaces the ERC and DRC stage outcomes from the latest build. */
export function DrcErcView({ build }: DrcErcViewProps) {
  if (!build) {
    return null
  }
  const rows: StageResult[] = (['erc', 'drc'] as const)
    .map((name) => build.stages.find((stage) => stage.name === name))
    .filter((stage): stage is StageResult => stage !== undefined)
  if (rows.length === 0) {
    return null
  }

  return (
    <div className="results">
      <div className="results__head">ERC / DRC</div>
      <ul className="results__issues">
        {rows.map((stage) => {
          const detail = stage.detail
          const skipped = detail.skipped === true
          const kind = skipped ? 'info' : stage.ok ? 'ok' : 'error'
          return (
            <li key={stage.name} className={`issue issue--${kind}`}>
              <code className="issue__code">{stage.name.toUpperCase()}</code>
              <span className="issue__message">
                {skipped
                  ? `skipped — ${String(detail.reason ?? 'not run')}`
                  : `${String(detail.errors ?? 0)} error(s), ` +
                    `${String(detail.warnings ?? 0)} warning(s)`}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
