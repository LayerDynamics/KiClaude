import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useRoute } from './lib/router'
import { ThreePage } from './pages/ThreePage'

/**
 * Root entry — selects between the editor (`/`) and the 3D
 * board viewer (`/three`) based on the hash route. See
 * `client/src/lib/router.tsx` for the routing primitive.
 */
function Root() {
  const route = useRoute()
  if (route === '/three') return <ThreePage />
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
