import { defineConfig, type Connect, type Plugin } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'
import tailwindcss from '@tailwindcss/vite'
import { createReadStream, statSync } from 'node:fs'
import { extname, join, normalize, resolve, sep } from 'node:path'

const EXAMPLES_ROOT = resolve(__dirname, '..', 'examples')

const KICAD_MIME: Record<string, string> = {
  '.kicad_pcb': 'text/plain; charset=utf-8',
  '.kicad_sch': 'text/plain; charset=utf-8',
  '.kicad_pro': 'application/json; charset=utf-8',
  '.kicad_sym': 'text/plain; charset=utf-8',
  '.kicad_mod': 'text/plain; charset=utf-8',
  '.kicad_wks': 'text/plain; charset=utf-8',
}

/**
 * Serve files under `<repo>/examples/**` at `/examples/**`. Used by
 * the M0-T-04 PCB viewport to load `examples/blinky/blinky.kicad_pcb`
 * via a stable URL in dev and in the built preview server.
 *
 * The middleware refuses paths that escape `EXAMPLES_ROOT` after
 * normalisation so the dev server can't be turned into a generic
 * filesystem reader.
 */
function examplesMiddleware(): Plugin {
  return {
    name: 'kiclaude-examples-mw',
    configureServer(server) {
      server.middlewares.use('/examples', buildHandler())
    },
    configurePreviewServer(server) {
      server.middlewares.use('/examples', buildHandler())
    },
  }

  function buildHandler(): Connect.NextHandleFunction {
    return (req, res, next) => {
      try {
        const rawUrl = req.url ?? '/'
        // `rawUrl` here is RELATIVE to /examples (Connect strips the
        // mount prefix).
        const url = new URL(rawUrl, 'http://kiclaude.local')
        const decoded = decodeURIComponent(url.pathname)
        const target = normalize(join(EXAMPLES_ROOT, decoded))
        if (!target.startsWith(EXAMPLES_ROOT + sep) && target !== EXAMPLES_ROOT) {
          res.statusCode = 403
          res.end('forbidden')
          return
        }
        const stat = statSync(target)
        if (stat.isDirectory()) {
          res.statusCode = 404
          res.end('directory listing not enabled')
          return
        }
        const ext = extname(target)
        const mime = KICAD_MIME[ext] ?? 'application/octet-stream'
        res.setHeader('Content-Type', mime)
        res.setHeader('Content-Length', stat.size.toString())
        res.setHeader('Cache-Control', 'no-cache')
        createReadStream(target).pipe(res)
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code === 'ENOENT') {
          res.statusCode = 404
          res.end('not found')
          return
        }
        next(err as Error)
      }
    }
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    tailwindcss(),
    examplesMiddleware(),
  ],
})
