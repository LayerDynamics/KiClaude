import { useEffect, useState } from 'react'
import { type DoctorCheck, getDoctor } from '../api/client'

/**
 * A compact environment-health badge — shows how many of the pipeline's
 * tool dependencies (kicad-cli, pcbnew, Freerouting, …) are present, with
 * the per-check detail in the tooltip.
 */
export function DoctorBadge() {
  const [checks, setChecks] = useState<DoctorCheck[] | null>(null)

  useEffect(() => {
    let cancelled = false
    getDoctor()
      .then((result) => {
        if (!cancelled) setChecks(result)
      })
      .catch(() => {
        if (!cancelled) setChecks([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (checks === null) {
    return <span className="doctor doctor--loading">env…</span>
  }
  if (checks.length === 0) {
    return <span className="doctor doctor--warn">env unavailable</span>
  }

  const okCount = checks.filter((check) => check.ok).length
  const allOk = okCount === checks.length
  const tooltip = checks
    .map((check) => `${check.ok ? '✓' : '✗'} ${check.name}: ${check.detail}`)
    .join('\n')

  return (
    <span
      className={`doctor doctor--${allOk ? 'ok' : 'warn'}`}
      title={tooltip}
    >
      env {okCount}/{checks.length}
    </span>
  )
}
