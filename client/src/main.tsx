import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useRoute, useShareToken } from './lib/router'
import { SharePage } from './pages/SharePage'
import { ThreePage } from './pages/ThreePage'

/**
 * Root entry — selects between the editor (`/`), the 3D board viewer
 * (`/three`), and the read-only share view (`/share/<token>`, FR-080)
 * based on the hash route. See `client/src/lib/router.tsx`.
 */
function Root() {
  const route = useRoute()
  const shareToken = useShareToken()
  if (route === '/three') return <ThreePage />
  if (route === '/share') return <SharePage token={shareToken} />
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
