# ki-mcp-pcb GUI

The browser front-end for ki-mcp-pcb — a Vite + React + TypeScript single-page
app that drives the text-to-PCB pipeline through the `ki-mcp-pcb-web` FastAPI
backend. See [`SPEC-1`](../../docs/specs/SPEC-1-gui-copilot.md) for the design
and [`docs/plans/2026-05-19-gui-copilot-g1.md`](../../docs/plans/2026-05-19-gui-copilot-g1.md)
for the milestone-G1 plan this implements.

Milestone **G1** (current): open and edit the CIR with live validation, run
the pipeline and watch each stage stream in, read the results, and download
the build artifacts. The Claude co-pilot chat arrives in **G2**.

## Running it

The GUI needs the backend running. Two ways:

**Development (hot reload):**

```bash
# terminal 1 — the backend API on 127.0.0.1:8765
uv run kimp serve

# terminal 2 — the Vite dev server (proxies /api to the backend)
uv run ki-mcp-pcb-gui          # or: npm run dev
```

**Production (one server):**

```bash
npm run build                  # emits dist/
uv run kimp serve              # serves dist/ at / and the API at /api
```

When `dist/` exists the backend serves it automatically; otherwise it falls
back to the legacy `/static` viewer.

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server with HMR; `/api` is proxied to `127.0.0.1:8765`. |
| `npm run build` | Type-check (`tsc`) + bundle to `dist/`. |
| `npm run test` | Vitest + React Testing Library component/integration tests. |
| `npm run lint` | ESLint over the TypeScript sources. |
| `npm run gen:types` | Regenerate `src/api/schema.ts` from the backend's OpenAPI schema. |

## API types

`src/api/schema.ts` is **generated** from the FastAPI OpenAPI schema by
`gen:types` — never hand-edit it. Change a backend endpoint, then run
`npm run gen:types`. CI fails if the committed `schema.ts` has drifted.
