# SPEC-1 — ki-mcp-pcb GUI Co-pilot

| | |
|---|---|
| **Spec** | SPEC-1 |
| **Title** | Browser GUI co-pilot — drive the text-to-PCB pipeline and Claude Code from one window |
| **Status** | Draft |
| **Author** | layerdynamics |
| **Created** | 2026-05-19 |
| **Supersedes** | — (first formal spec; complements [`SPEC.md`](../../SPEC.md), the pipeline design) |
| **Affected packages** | `ki_mcp_pcb_gui` (frontend), `ki_mcp_pcb_web` (backend), `ki_mcp_pcb_server` (MCP toolset), `ki_mcp_pcb_core` (read-only consumer) |

---

## 1. Summary

`ki-mcp-pcb` turns plain-text circuit descriptions into manufacturable KiCad
PCBs. Today it is driven three ways — the `kimp` CLI, the MCP server (Claude
Code in a terminal/IDE), and a read-only browser viewer (`ki_mcp_pcb_web`).
There is no single surface where a user can both *edit a design* and *converse
with Claude about it*.

This spec defines a **browser GUI co-pilot**: a single window in which a user
can author the CIR, run the full pipeline, inspect every result, and talk to
Claude Code — with Claude able to drive the whole pipeline agentically. A user
who wants to **never open a terminal** can work entirely in the GUI; a user who
prefers the CLI/IDE loses nothing, because the GUI is an additional surface
over the same core library and MCP tools.

The frontend is the existing `packages/ki_mcp_pcb_gui` Vite + React +
TypeScript app; the backend is the existing `packages/ki_mcp_pcb_web` FastAPI
service, extended. Claude is integrated server-side via the **Claude Agent
SDK**, with the `ki_mcp_pcb_server` MCP server wired in as its toolset — so
Claude-in-the-GUI has exactly the tools Claude-in-a-terminal has.

## 2. Problem statement

- **No unified surface.** Editing a `.cir.yaml`, running `kimp build`, reading
  DRC JSON, and asking Claude for help are four disjoint activities across an
  editor, a terminal, and a browser tab.
- **The browser viewer is read-only.** `ki_mcp_pcb_web` parses and displays a
  board (`/api/validate`, `/api/diff`, `/api/impedance`) but cannot edit it,
  run a build, or hold a conversation.
- **Claude Code requires a terminal/IDE.** A hardware engineer who is not a
  CLI user has no way to use the project's marquee capability — conversational,
  agentic PCB design.
- **No feedback loop in one place.** The natural workflow — describe intent →
  see CIR → build → read ERC/DRC → adjust → rebuild — currently spans tools,
  so the iteration loop is slow and easy to lose track of.

## 3. Goals and non-goals

### 3.1 Goals

- **G-1** A single browser window to author the CIR, run the pipeline, inspect
  results, and converse with Claude.
- **G-2** Claude is a **full agent** inside the GUI: it can run every MCP
  pipeline tool, read and write the working CIR file, and iterate — never
  requiring the user to drop to a terminal.
- **G-3** Three synchronized CIR authoring modes — structured form, raw
  YAML/`.ato` text, and natural-language chat — all editing the same file.
- **G-4** Every pipeline stage (parse → validate → sourcing → synthesize →
  populate → DRC/ERC → fab) is runnable and its structured result is rendered
  in the GUI, including downloadable artifacts (`.kicad_pcb`, fab zip, reports).
- **G-5** Reuse the existing core: the GUI/backend add **no** circuit logic;
  they call `ki_mcp_pcb_core` and the MCP tools. CIR remains the contract.
- **G-6** Ship as a first-class workspace package runnable with one command
  (`uv run ki-mcp-pcb-gui` for the frontend, `kimp serve` for the backend).

### 3.2 Non-goals

- **NG-1** Multi-user / hosted operation. v1 is **local single-user** on
  `127.0.0.1` — one user, their machine, their Claude and KiCad installs. No
  accounts, no auth, no tenant isolation. (See §11 Open Questions for a future
  hosted track.)
- **NG-2** A schematic-capture or PCB-layout *editor*. The GUI previews KiCad
  output; it does not replace KiCad's editors.
- **NG-3** Replacing the CLI or the MCP server. They remain first-class; the
  GUI is an additional surface.
- **NG-4** Autonomous RF/DDR routing. Per `SPEC.md §6`, M4 is co-pilot only;
  the GUI surfaces sign-off gates but never flips them on the user's behalf.
- **NG-5** Re-implementing pipeline logic in TypeScript. The frontend is a
  view; all computation stays in Python core.

## 4. Users and use cases

**Primary user:** a hardware engineer or maker running ki-mcp-pcb on their own
machine who wants a visual, conversational way to design a board.

| # | Use case |
|---|---|
| UC-1 | "Describe a board in English, watch Claude draft the CIR, review it in the form editor, build it, download the fab zip." — terminal never opened. |
| UC-2 | "Open an existing `.cir.yaml`, edit a net in the YAML pane, see live validation errors, rebuild." |
| UC-3 | "Build fails DRC — ask Claude in chat why; Claude reads the DRC report, explains, edits the CIR, rebuilds." |
| UC-4 | "Diff two revisions of a board and see what changed." |
| UC-5 | "Check controlled-impedance nets and decoupling before sign-off." |

## 5. Requirements

### 5.1 Functional requirements

**CIR authoring (G-3)**

- **FR-1** Load a CIR from disk into a working session; save edits back.
- **FR-2** Raw text editor for YAML / `.ato` with syntax highlighting and
  **live validation** (debounced call to the parse+validate endpoint;
  CIR001…CIR110 issues shown inline with severity).
- **FR-3** Structured form editor for components, nets, stackup, fab target,
  and constraints — backed by the typed CIR Pydantic schema.
- **FR-4** All three modes (form, text, chat) edit one canonical CIR file; a
  change in any mode re-renders the others. The text pane is the source of
  truth on disk; the form serializes to it; chat edits go through it.
- **FR-5** New-project flow: start from a natural-language description (mirrors
  the `pcb-new` slash command and the `parse_intent` MCP tool).

**Pipeline (G-4)**

- **FR-6** Run the full build (`pipeline.build`) and each stage individually;
  stream `BuildStageResult`s to the GUI as they complete.
- **FR-7** Render each stage's structured result: validation issues, sourcing
  table, synthesis file list, populate report, DRC/ERC violations (severity,
  type, description, items), fab package contents.
- **FR-8** Download generated artifacts: `.kicad_pro` / `.kicad_pcb` /
  `.kicad_sch`, `.net`, DRC/ERC JSON, the fab zip.
- **FR-9** Surface environment health (`doctor` — kicad-cli, pcbnew, kiutils,
  Freerouting, kipy) so the user understands which stages can run.
- **FR-10** Diff two CIRs and render the structured diff (reuses `/api/diff`).
- **FR-11** Signal-integrity views: per-net impedance (reuses `/api/impedance`),
  decoupling check, return-path check (reuses the corresponding MCP tools).

**Claude co-pilot (G-1, G-2)**

- **FR-12** A chat panel holding a streamed conversation with Claude.
- **FR-13** Claude runs server-side via the Claude Agent SDK, configured with
  the `ki_mcp_pcb_server` MCP server as its toolset — the GUI agent has the
  same ~24 tools (`tool_validate_cir`, `tool_synthesize`, `tool_build`,
  `tool_drc`, `tool_export_fab`, …) as Claude Code in a terminal.
- **FR-14** The agent may read and write the working CIR file and run any
  pipeline tool — full agentic control (G-2).
- **FR-15** Agent activity is rendered transparently: assistant text,
  tool-use calls, and tool results all appear in the conversation stream.
- **FR-16** Approval gates: actions that are irreversible or outward-facing —
  writing the CIR file, fab export, and any `Board.signoff.*` change — surface
  an explicit approve/reject prompt in the GUI before execution. Per
  `CLAUDE.md`, an LLM may not flip sign-off flags; the GUI enforces a human
  click.
- **FR-17** When the agent edits the CIR, the editor panes refresh to the new
  content (FR-4).

### 5.2 Non-functional requirements

- **NFR-1 — Local & private.** Backend binds `127.0.0.1` only. No data leaves
  the machine except the user's own Claude API/agent traffic to Anthropic.
- **NFR-2 — No logic duplication.** The backend is a thin transport over
  `ki_mcp_pcb_core` + the MCP tools (consistent with `ki_mcp_pcb_web` today).
- **NFR-3 — Stateless tools.** MCP tools stay stateless (`CLAUDE.md` rule 3);
  GUI session state lives in the backend process + files on disk, not in tools.
- **NFR-4 — Structured contracts.** Backend endpoints and the agent stream
  return structured JSON; the GUI does the narration (`CLAUDE.md` rule 4).
- **NFR-5 — Graceful degradation.** Stages needing KiCad/Freerouting that
  aren't installed are reported as skipped-with-reason, never crash (matches
  `pipeline.build` today).
- **NFR-6 — Workspace-native.** `ki_mcp_pcb_gui` stays a uv workspace member;
  lint (`ruff`), types (`mypy` for Python, `tsc` for TS), and tests run in CI.
- **NFR-7 — Bounded filesystem scope.** The agent and pipeline operate within a
  single configured working directory; paths outside it are rejected.
- **NFR-8 — Responsive streaming.** Agent tokens and pipeline stage results
  appear incrementally; a long build never blocks the UI.

## 6. Architecture

### 6.1 Component overview

```text
┌───────────────────────────── Browser ──────────────────────────────┐
│  ki_mcp_pcb_gui  (Vite + React + TypeScript)                        │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────────┐ │
│  │ CIR editor │ │ Pipeline   │ │ Results /  │ │ Claude chat      │ │
│  │ form/YAML  │ │ run + stage│ │ DRC·ERC·BOM│ │ (streamed)       │ │
│  └────────────┘ │ status     │ │ artifacts  │ └──────────────────┘ │
│         │       └────────────┘ └────────────┘          │           │
└─────────┼──────────────── HTTP + SSE + WebSocket ───────┼───────────┘
          │                                               │
┌─────────▼───────────────────────────────────────────────▼──────────┐
│  ki_mcp_pcb_web  (FastAPI, 127.0.0.1)                               │
│  REST: /api/cir /api/validate /api/build /api/diff /api/impedance   │
│  Stream: SSE /api/build/stream   WS /api/agent                      │
│  ┌───────────────────────┐   ┌───────────────────────────────────┐ │
│  │ Pipeline runner        │   │ Agent session manager             │ │
│  │ → ki_mcp_pcb_core      │   │ → Claude Agent SDK                │ │
│  │   pipeline.build(...)  │   │   + MCP server: ki_mcp_pcb_server │ │
│  └───────────────────────┘   └───────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
          │                                               │
          ▼                                               ▼
   ki_mcp_pcb_core (CIR, synthesis, validation,    Anthropic API
   KiCad backend, pipeline)  +  KiCad / kicad-cli   (Claude models)
```

### 6.2 Key decisions (resolved during discovery)

| Decision | Choice | Rationale |
|---|---|---|
| Claude integration | **Claude Agent SDK, embedded in the FastAPI backend** | Purpose-built for agent loops + streaming; the MCP server plugs in directly as a toolset; no terminal needed; structured events (vs. scraping CLI stdout). |
| Claude scope | **Full agentic control** | Goal G-2 — a user must be able to never touch a terminal. Claude runs the pipeline and edits the CIR; the GUI gates only the irreversible actions (FR-16). |
| Deployment | **Local single-user, `127.0.0.1`** | Matches how the pipeline already runs (KiCad, pcbnew, the user's Claude auth are all local). Hosted multi-user is a separate future spec. |
| CIR authoring | **Form + YAML + chat, synchronized** | G-3 — different users want different control; the canonical CIR file keeps them consistent. |
| Frontend | **Reuse `ki_mcp_pcb_gui` (Vite/React/TS)** | Already scaffolded and wired into the workspace; replace the boilerplate `App.tsx` with the real UI. |
| Backend | **Extend `ki_mcp_pcb_web` (FastAPI)** | Already exists with `/api/validate` etc.; add session, build-stream, agent, and CIR-CRUD endpoints. |

### 6.3 Data flow — the iteration loop

1. User describes intent in chat, or opens/edits a CIR file.
2. Backend persists the working CIR to disk (the canonical file).
3. User (or Claude) triggers `build`; the pipeline runner calls
   `pipeline.build()` and streams `BuildStageResult`s over SSE.
4. The GUI renders each stage; failures (ERC/DRC errors, unresolved MPNs)
   show structured detail.
5. User asks Claude about a failure; the agent reads the report file via an
   MCP tool, explains, proposes a CIR edit.
6. The CIR write is gated (FR-16); on approval the file changes and all editor
   panes refresh (FR-4, FR-17). Loop to step 3.

### 6.4 Backend API surface

Existing (keep): `GET /api/version`, `POST /api/validate`, `POST /api/diff`,
`POST /api/impedance`.

New:

| Method / path | Purpose |
|---|---|
| `GET /api/cir` | Return the working CIR (raw text + parsed board summary + validation report). |
| `PUT /api/cir` | Replace the working CIR text; parse + validate; return the new state. |
| `POST /api/build` | Run `pipeline.build` (optionally a single stage); returns the final `BuildResult`. |
| `GET /api/build/stream` (SSE) | Stream `BuildStageResult` events as stages complete. |
| `GET /api/doctor` | Environment health (`pipeline.doctor`). |
| `GET /api/artifacts` | List generated files in the working dir. |
| `GET /api/artifacts/{name}` | Download one artifact (path-validated against the working dir, NFR-7). |
| `WS /api/agent` | Bidirectional Claude session: user messages in; streamed agent events out (`text`, `tool_use`, `tool_result`, `approval_request`, `cir_changed`, `error`). |

### 6.5 Claude agent integration

- The backend adds a dependency on the **Claude Agent SDK** (`claude-agent-sdk`,
  Python) — a new optional extra on `ki_mcp_pcb_web` (`agent`), since the
  pipeline-only GUI must still run without it.
- One agent **session** per WebSocket connection. The session is created with:
  - the `ki_mcp_pcb_server` MCP server registered as a tool source (the agent
    gets the same ~24 `tool_*` functions defined in
    `packages/ki_mcp_pcb_server/src/ki_mcp_pcb_server/tools.py`);
  - a system prompt that states the CIR-is-the-contract rule, the milestone
    model, the working directory, and that sign-off flags are human-only;
  - the working directory as the agent's filesystem scope (NFR-7).
- Agent events are translated to the WebSocket event types in §6.4 and
  rendered in the chat panel (FR-15).
- **Approval gating (FR-16):** writes to the CIR file, `tool_export_fab`, and
  any edit touching `Board.signoff.*` pause for a GUI approve/reject. This is
  enforced backend-side (a tool-permission callback), not merely hidden in the
  UI, so it holds even if the frontend is bypassed.
- **Auth:** the SDK uses the user's existing Anthropic credentials
  (`ANTHROPIC_API_KEY` env or the local Claude Code login). Absent
  credentials, the chat panel shows a clear "connect Claude" message; the
  pipeline GUI still works.
- **Model:** default to the latest Claude model the SDK exposes; configurable.

### 6.6 Frontend structure

Replace the boilerplate in `packages/ki_mcp_pcb_gui/src/`:

- `App.tsx` — layout shell: CIR editor (left), pipeline + results (center),
  Claude chat (right); panels resizable/collapsible.
- `api/` — typed client for the §6.4 endpoints; SSE + WebSocket hooks.
- `cir/` — form editor, YAML/`.ato` text editor (with live-validation hook),
  mode-sync logic (FR-4).
- `pipeline/` — run controls, per-stage status, artifact list/downloads.
- `results/` — validation, sourcing, BOM, DRC/ERC, diff, impedance views.
- `chat/` — streamed conversation, tool-call rendering, approval prompts.

TypeScript types for the CIR and API payloads are generated from the backend
(FastAPI OpenAPI schema) so the frontend stays in lock-step with the Pydantic
models — no hand-maintained duplicate types.

## 7. Implementation plan

Each milestone is independently shippable and leaves the suite green
(`uv run pytest`, `ruff`, `mypy`, `tsc`).

### G1 — Pipeline GUI, no Claude

- Backend: add `GET/PUT /api/cir`, `POST /api/build` + `GET /api/build/stream`
  (SSE), `GET /api/doctor`, `GET /api/artifacts[/{name}]`.
- Frontend: replace boilerplate `App.tsx`; YAML/`.ato` text editor with live
  validation; pipeline run + streamed stage status; results views
  (validation, sourcing, DRC/ERC, artifacts).
- Tests: backend endpoint tests (FastAPI `TestClient`); a frontend smoke test
  driving load → edit → build → see-result.
- **Exit:** a user can open a CIR, edit it, build, and download a fab zip —
  in the browser, no Claude.

### G2 — Claude agent integration

- Backend: `ki_mcp_pcb_web[agent]` extra (`claude-agent-sdk`); agent session
  manager; `WS /api/agent`; MCP server wired in as the toolset; tool-permission
  callback for the approval gates.
- Frontend: chat panel with streamed text + tool-call rendering + approval
  prompts; `cir_changed` refreshes the editor.
- Tests: agent session lifecycle with a stubbed SDK transport (no live API in
  CI); approval-gate enforcement test.
- **Exit:** a user can hold a conversation, and Claude can run the pipeline and
  (with approval) edit the CIR — entirely in the GUI.

### G3 — Structured form editor + full results

- Frontend: structured component/net/stackup/fab form editor, synchronized
  with the YAML pane (FR-4); BOM, diff, and impedance/decoupling/return-path
  views.
- Backend: generate + publish the OpenAPI-derived TypeScript types.
- Tests: form↔YAML round-trip test; diff/impedance view tests.
- **Exit:** all three CIR authoring modes work and stay in sync.

### G4 — Co-pilot polish & sign-off

- New-project-from-intent flow (FR-5); M4 RF/DDR/BGA sign-off surfaced as
  explicit human-gated controls; session persistence (reopen the last working
  project); KiCad board preview (e.g. embed KiCanvas for the populated PCB).
- Tests: sign-off gate is human-only (agent cannot flip it); intent flow test.
- **Exit:** the GUI is a complete co-pilot; the terminal is genuinely optional.

## 8. Testing strategy

- **Backend:** FastAPI `TestClient` for every endpoint; SSE/WebSocket tested
  with the framework's streaming test support; the agent path tested against a
  stubbed Agent-SDK transport so CI needs no Anthropic key (mirrors how
  `test_kipy_placer.py` injects a fake KiCad client).
- **Frontend:** component tests for the editors and result views; one
  end-to-end smoke test (load → edit → build → result) under the workspace's
  TS toolchain.
- **Integration:** reuse the existing golden/e2e suite — the GUI calls the same
  `pipeline.build`, so `tests/test_real_kicad_end_to_end.py` already covers the
  pipeline the GUI exercises.
- **Gate:** no milestone merges unless `pytest`, `ruff`, `mypy`, and the TS
  build/lint all pass.

## 9. Risks and mitigations

| # | Risk | Mitigation |
|---|---|---|
| R-1 | Agent SDK API/version drift | Pin `claude-agent-sdk`; isolate all SDK calls behind one `agent/` module so a shape change is one-place editable (same stance as the `kipy_placer` IPC wrapper). |
| R-2 | Live Claude calls would make CI flaky/costly | Stub the SDK transport in tests; never call the live API in CI — only in an opt-in, key-gated job. |
| R-3 | Agentic file/tool access is powerful | NFR-7 working-dir sandbox; FR-16 backend-enforced approval gates for irreversible/outward-facing actions; sign-off stays human-only. |
| R-4 | Frontend/Pydantic schema drift | Generate TS types from the backend OpenAPI schema; never hand-maintain duplicates. |
| R-5 | Streaming/WebSocket complexity (reconnects, partial events) | Idempotent event model with sequence numbers; the GUI can re-request current state (`GET /api/cir`, `GET /api/build`) after a drop. |
| R-6 | KiCad/Freerouting absent on the user's machine | `GET /api/doctor` surfaces it; stages skip-with-reason; the GUI shows which stages are available — never a hard crash (NFR-5). |
| R-7 | Scope creep toward a hosted multi-user product | NG-1 fixes v1 as local single-user; a hosted track is a separate spec, not a stretch goal here. |

## 10. Out of scope (restated)

Multi-user/hosted operation, authentication, a schematic/layout editor,
autonomous RF/DDR routing, and any re-implementation of pipeline logic in
TypeScript. See §3.2.

## 11. Open questions

| # | Question | Owner | Resolution path |
|---|---|---|---|
| OQ-1 | Embed KiCanvas for live `.kicad_pcb` preview, or link out to KiCad? | layerdynamics | Spike in G4; decide on KiCanvas bundle size + license. |
| OQ-2 | Should the working CIR be a single file or a project folder of revisions? | layerdynamics | Decide before G1 API freeze; default to single file + on-disk diff. |
| OQ-3 | Future hosted multi-user track — sandboxing, job queue, auth model | layerdynamics | Separate spec (SPEC-2) once v1 ships; explicitly deferred. |
| OQ-4 | Exact Agent-SDK session persistence model across GUI restarts | layerdynamics | Resolve in G4 (session persistence task). |

## 12. Acceptance criteria

The spec is satisfied when:

1. `kimp serve` starts the backend and `uv run ki-mcp-pcb-gui` serves the
   frontend; opening the browser shows the co-pilot window.
2. A user can complete UC-1 — describe a board, have Claude draft the CIR,
   review/edit it, build it, and download a fab zip — **without a terminal**.
3. Claude in the GUI can run every pipeline stage and (with GUI approval) edit
   the CIR; sign-off flags cannot be set by the agent.
4. The CIR form, YAML, and chat modes stay synchronized to one file.
5. All four milestones' tests pass; `pytest`, `ruff`, `mypy`, and the TS
   build/lint are green; no pipeline logic is duplicated outside
   `ki_mcp_pcb_core`.
