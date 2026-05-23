import type { ValidationSummary } from '../api/client'

interface ValidationViewProps {
  validation: ValidationSummary | null
  parseError: string | null
}

/** Renders the CIR validation outcome — parse errors, or the CIR001…CIR110 issues. */
export function ValidationView({ validation, parseError }: ValidationViewProps) {
  if (parseError) {
    return (
      <div className="results results--error">
        <div className="results__head results__head--fail">Parse error</div>
        <pre className="results__pre">{parseError}</pre>
      </div>
    )
  }
  if (!validation) {
    return <p className="pane__placeholder">Load a CIR to see validation.</p>
  }

  const summary = validation.ok
    ? 'clean'
    : `${validation.errors} error${validation.errors === 1 ? '' : 's'}`
  const warnings =
    validation.warnings > 0
      ? `, ${validation.warnings} warning${validation.warnings === 1 ? '' : 's'}`
      : ''

  return (
    <div className="results">
      <div
        className={`results__head results__head--${validation.ok ? 'ok' : 'fail'}`}
      >
        Validation — {summary}
        {warnings}
      </div>
      {validation.issues.length > 0 && (
        <ul className="results__issues">
          {validation.issues.map((issue, index) => {
            const severity = String(issue.severity ?? 'info')
            return (
              <li key={index} className={`issue issue--${severity}`}>
                <code className="issue__code">{String(issue.code ?? '')}</code>
                <span className="issue__message">
                  {String(issue.message ?? '')}
                </span>
                {issue.where != null && (
                  <span className="issue__where">{String(issue.where)}</span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
