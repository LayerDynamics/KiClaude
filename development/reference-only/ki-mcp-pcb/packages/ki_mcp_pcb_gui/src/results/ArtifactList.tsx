import { useEffect, useState } from 'react'
import { type Artifact, artifactUrl, getArtifacts } from '../api/client'

interface ArtifactListProps {
  /** Bump this (e.g. after a build) to re-fetch the artifact list. */
  refreshKey: number
}

/** Lists the generated build artifacts, each a download link. */
export function ArtifactList({ refreshKey }: ArtifactListProps) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([])

  useEffect(() => {
    let cancelled = false
    getArtifacts()
      .then((items) => {
        if (!cancelled) setArtifacts(items)
      })
      .catch(() => {
        if (!cancelled) setArtifacts([])
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  if (artifacts.length === 0) {
    return null
  }
  return (
    <div className="results">
      <div className="results__head">Artifacts — {artifacts.length} file(s)</div>
      <ul className="results__artifacts">
        {artifacts.map((artifact) => (
          <li key={artifact.path}>
            <a href={artifactUrl(artifact.path)} download>
              {artifact.path}
            </a>
            <span className="artifact__size">{artifact.size} B</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
