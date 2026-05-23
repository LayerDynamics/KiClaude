# Implementation Plan — GUI Co-pilot, Milestone G1

| | |
|---|---|
| **Date** | 2026-05-19 |
| **Implements** | [`SPEC-1`](../specs/SPEC-1-gui-copilot.md) — milestone **G1: Pipeline GUI, no Claude** |
| **Packages** | `ki_mcp_pcb_web` (FastAPI backend), `ki_mcp_pcb_gui` (Vite/React/TS frontend) |
| **Out of scope for G1** | Claude agent / chat (G2), structured form editor (G3), sign-off & previews (G4) |

## Goal

A user can, in the browser: open a CIR file, edit it as YAML/`.ato` with live
validation, run the full pipeline and watch each stage stream in, read the
structured results (validation, sourcing, DRC/ERC), and download the generated
artifacts — **with no Claude and no terminal beyond starting the server**.

## Discovery decisions

| Question | Decision |
|---|---|
| Test discipline | Each task ships with its tests; the suite stays green task-by-task. |
| Frontend tests | Vitest + React Testing Library set up in G1, wired into CI. |
| Plan shape | One combined plan — backend and frontend interleave as thin vertical slices. |
| TypeScript types | OpenAPI→TS codegen stood up in G1; frontend types are generated from the backend schema. |

## Context (verified against the codebase)

- `ki_mcp_pcb_web/src/ki_mcp_pcb_web/server.py` — FastAPI `app` with
  `/api/version`, `/api/validate`, `/api/diff`, `/api/impedance`; serves a
  static viewer; `run()` boots uvicorn on `127.0.0.1:8765`. Started via
  `kimp serve` or `ki-mcp-pcb-web`.
- `ki_mcp_pcb_core.pipeline.build(source, out_dir, *, run_route=False)` returns
  `BuildResult(ok, stages, out_dir)`; each `BuildStageResult(name, ok, detail)`.
  `pipeline.doctor()` returns `list[DoctorCheck(name, ok, detail)]`.
- `ki_mcp_pcb_gui/` — Vite + React + TS scaffold; `package.json` has
  `dev`/`build`/`lint`/`preview` scripts, **no `test` script**; `start.py` is
  the Python launcher; it is a uv workspace member.
- The working model is **local single-user** (SPEC-1 §3.2): the backend tracks
  one *working directory* containing one *working CIR file*; builds write into
  a `build/` subdirectory of it.
- CIR is the contract — the backend adds no circuit logic, only transport
  (`CLAUDE.md` rule; `ki_mcp_pcb_web` already follows this).

## Working-file model (resolves SPEC-1 OQ-2 for G1)

The backend holds a single **working directory** (default: a `gui-workspace/`
under the repo, overridable by env/CLI) with one **working CIR file**
(`board.cir.yaml`). `GET/PUT /api/cir` read/write that file. Builds run into
`<workdir>/build/`. A project folder of revisions is deferred (OQ-2 stays open
for a later milestone).

---

## Tasks

Tasks are ordered so a thin vertical slice works end-to-end as early as
possible. Every task ships its own tests and leaves `pytest`, `ruff`, `mypy`,
and the TS lint/test green.

### T1 — Backend: working-CIR session + `GET`/`PUT /api/cir`

- Add a `session` module to `ki_mcp_pcb_web` holding the working-directory +
  working-CIR-file paths (env override `KIMP_GUI_WORKDIR`, sensible default).
- `GET /api/cir` → `{ "text": <raw>, "board": <_board_summary>, "validation":
  <report>, "exists": bool }`. Reuses `_parse_source` + `validate_board` +
  `_board_summary` already in `server.py`.
- `PUT /api/cir` (body: `{ "text": str }`) → write the file, parse, validate,
  return the same shape. Parse failure → HTTP 400 with the parser message
  (do not write a syntactically broken file silently — return the error).
- **Tests** (`packages/ki_mcp_pcb_web/tests/test_api_cir.py`, FastAPI
  `TestClient`): GET on a fresh workdir; PUT a valid CIR then GET it back;
  PUT invalid YAML → 400; PUT a CIR with validation errors → 200 with
  `validation.ok == false`.
- **Done when** the CIR file round-trips through the API with validation.

### T2 — Frontend tooling: Vitest + RTL + OpenAPI→TS codegen

- Add to `ki_mcp_pcb_gui/package.json`: `vitest`, `@testing-library/react`,
  `@testing-library/jest-dom`, `jsdom`, and `openapi-typescript`; scripts
  `test` (`vitest run`) and `gen:types`.
- Add a tiny backend helper — `ki_mcp_pcb_web` module-level script /
  console-entry that dumps `app.openapi()` to `openapi.json` — so codegen
  needs no running server.
- `gen:types` runs the dump, then `openapi-typescript openapi.json -o
  src/api/schema.ts`. Commit the generated `schema.ts`; CI re-runs `gen:types`
  and fails on drift.
- Update `start.py`: keep `dev`/`build`/`preview`; the launcher already wraps
  `npm run`, so `test` is reachable as `npm run test` — no launcher change
  needed beyond confirming.
- **Tests**: a trivial Vitest sanity test (`src/api/__tests__/smoke.test.ts`)
  proving the runner + jsdom work.
- **Done when** `npm run test` and `npm run gen:types` both succeed and
  `schema.ts` exists with the T1 endpoints typed.

### T3 — Frontend: app shell + API client + CIR text editor (slice 1)

- Replace boilerplate `src/App.tsx` with the three-pane shell (editor / center
  / right) from SPEC-1 §6.6; right pane is a placeholder until G2.
- `src/api/client.ts` — typed `fetch` wrapper using `schema.ts`; functions
  `getCir()`, `putCir(text)`.
- `src/cir/CirEditor.tsx` — a YAML/`.ato` text editor (textarea-based with
  monospace + line numbers; a full code-editor component can come in G3)
  that loads `GET /api/cir` on mount and **autosaves** via `PUT /api/cir` on
  a debounced change.
- **Tests** (`src/cir/__tests__/CirEditor.test.tsx`, RTL): renders loaded CIR
  text; editing triggers a debounced `putCir`; mock the API client.
- **Done when** opening the browser shows the working CIR and edits persist.

### T4 — Backend: `POST /api/build`, `GET /api/build/stream` (SSE), `GET /api/doctor`

- `GET /api/doctor` → `pipeline.doctor()` as JSON.
- `POST /api/build` (body: `{ "run_route": bool }`) → run `pipeline.build` on
  the working CIR into `<workdir>/build/`; return the final `BuildResult`
  (stages + ok + out_dir).
- `GET /api/build/stream` (SSE) → run the build and emit one `event: stage`
  per `BuildStageResult` as it completes, then `event: done` with the
  `BuildResult`. Implement by running `pipeline.build` and yielding stage
  results; since `build()` is synchronous, run it in a worker thread and
  push stage results onto an async queue the SSE generator drains.
- **Tests** (`test_api_build.py`): `doctor` returns checks; `POST /api/build`
  on `examples/blinky.yaml` content returns a `BuildResult` with the expected
  stage names; the SSE endpoint emits a `stage` event per stage then `done`.
  KiCad-gated stages are asserted to *skip cleanly* when kicad-cli is absent
  (monkeypatch `_kicad_cli.is_available`), matching `test_end_to_end.py`.
- **Done when** a build is runnable via the API and streams stage-by-stage.

### T5 — Frontend: pipeline panel (slice 2)

- `src/pipeline/PipelinePanel.tsx` — a "Build" button, a per-stage status list
  fed by an EventSource on `/api/build/stream` (stage → ok/fail/skipped with
  its `detail`), and an overall result banner.
- `src/pipeline/DoctorBadge.tsx` — surfaces `GET /api/doctor` so the user sees
  which stages can run (kicad-cli, pcbnew, …).
- Extend `src/api/client.ts` with `getDoctor()` and an SSE helper.
- **Tests** (RTL): mock an `EventSource`; clicking Build renders streamed
  stages; a failed stage renders its detail; the doctor badge renders checks.
- **Done when** a user runs a build in the browser and watches it stream.

### T6 — Backend: artifact listing + download

- `GET /api/artifacts` → list files under `<workdir>/build/` (name, size,
  relative path).
- `GET /api/artifacts/{name}` → `FileResponse` for one artifact. **Reject any
  path that resolves outside `<workdir>/build/`** (SPEC-1 NFR-7) — resolve and
  check `is_relative_to` before serving; 404 otherwise.
- **Tests** (`test_api_artifacts.py`): after a build, listing shows the
  `.kicad_pcb` / `.net` / report files; download returns the bytes; a
  `../`-traversal path → 404.
- **Done when** generated files are listable and downloadable, sandboxed.

### T7 — Frontend: results views + inline validation (slice 3)

- `src/results/ValidationView.tsx` — render `validation` issues (CIR001…CIR110)
  with severity; show inline in/next to the editor as the user types
  (the autosave `PUT /api/cir` already returns the report — T1).
- `src/results/SourcingView.tsx`, `src/results/DrcErcView.tsx` — render the
  `sourcing` table and the `drc`/`erc` stage details (severity, type,
  description, items) from the build result.
- `src/results/ArtifactList.tsx` — list `GET /api/artifacts`, each a download
  link to `GET /api/artifacts/{name}` (incl. the fab zip).
- **Tests** (RTL): each view renders representative payloads; an errored
  validation report shows error styling; artifact links point at the right
  URLs.
- **Done when** every G1 result is visible and artifacts are downloadable.

### T8 — Integration, serving, and CI wiring

- Decide + implement how the built frontend is served: `npm run build` emits
  `dist/`; point `ki_mcp_pcb_web`'s static mount at `ki_mcp_pcb_gui/dist`
  when present (so `kimp serve` serves the real GUI), while `npm run dev`
  remains the hot-reload dev path against the same API. Document both in
  `ki_mcp_pcb_gui/README.md` (replace the Vite boilerplate README).
- CI (`.github/workflows/ci.yml`): add a job (or steps) that runs
  `npm ci`, `npm run lint`, `npm run test`, `npm run build`, and
  `gen:types` drift check for `ki_mcp_pcb_gui`.
- End-to-end smoke: a backend test that drives `GET /api/cir` → `PUT` an
  edit → `POST /api/build` → `GET /api/artifacts` in sequence.
- **Tests**: the e2e smoke test above; CI green on the new frontend job.
- **Done when** `kimp serve` serves the built GUI, the dev flow is documented,
  and CI exercises the frontend.

---

## Verification gate (whole milestone)

G1 is complete when:

1. `uv run pytest`, `uv run ruff check .`, `uv run mypy packages` are green.
2. `cd packages/ki_mcp_pcb_gui && npm run lint && npm run test && npm run build`
   are green.
3. Starting the backend and opening the browser, a user completes UC-2 from
   SPEC-1 — open a CIR, edit it with live validation, rebuild — without a
   terminal beyond launching the server.
4. No pipeline logic was added outside `ki_mcp_pcb_core` (SPEC-1 NFR-2).

## Risks specific to G1

| Risk | Mitigation |
|---|---|
| `pipeline.build` is synchronous and slow; blocking the event loop | T4 runs it in a worker thread and streams stage results over an async queue. |
| SSE/EventSource flakiness in tests | Test the stream with FastAPI's streaming `TestClient` support; mock `EventSource` on the frontend. |
| Generated `schema.ts` drifting from the backend | `gen:types` is a CI drift check (T2); types are never hand-edited. |
| Artifact endpoint path traversal | T6 resolves and bounds every path under `<workdir>/build/` (NFR-7) with an explicit test. |
| Autosave surprising the user / clobbering the file on a parse error | `PUT /api/cir` returns 400 and does **not** write when the text won't parse (T1). |

## Task dependency order

`T1 → T2 → T3` (slice 1: CIR round-trips) → `T4 → T5` (slice 2: build streams)
→ `T6 → T7` (slice 3: artifacts + results) → `T8` (serving + CI). T2 may run
in parallel with T1; everything else is sequential.
