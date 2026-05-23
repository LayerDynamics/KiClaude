# Implementation Plan — GUI Co-pilot, Milestone G4

| | |
|---|---|
| **Date** | 2026-05-20 |
| **Implements** | [`SPEC-1`](../specs/SPEC-1-gui-copilot.md) — milestone **G4: Co-pilot polish & sign-off** |
| **Builds on** | G3 ([`2026-05-19-gui-copilot-g3.md`](./2026-05-19-gui-copilot-g3.md)) — complete |
| **Packages** | `ki_mcp_pcb_web` (backend), `ki_mcp_pcb_gui` (frontend) |

## Goal

The terminal becomes genuinely optional. The GUI gains a new-project flow
that starts from a natural-language description (FR-5), explicit
human-only sign-off controls for the M4 RF/DDR/BGA gates, a persisted
working-directory choice that survives launches, and an embedded
KiCanvas preview of the populated PCB.

## Architecture (resolved)

- **Intent → CIR via `parse_nl`.** A new `POST /api/parse_intent` wraps
  `ki_mcp_pcb_core.parsers.nl.parse_nl` (existing flow used by the
  `parse_intent` MCP tool), returning the draft Board + YAML. The
  endpoint surfaces `NLParserUnavailableError` (no SDK / no
  `ANTHROPIC_API_KEY`) as 503 with the structured detail so the GUI can
  show "configure Anthropic to use intent-to-CIR." Tests stub the
  `parse_nl` symbol in `server.py`, never call the live API in CI.
- **Sign-off via a focused PATCH, not the form's full Board write.**
  Flipping `Board.signoff.rf_reviewed` etc. would otherwise force the
  form editor's PATCH path through a full Board re-serialize and reopen
  the form-vs-text race for the rest of the board. Instead, a new
  `PATCH /api/cir/signoff` takes only the four flags + reviewer/
  reviewed_at, applies them via `Signoff.model_copy(update=...)`,
  re-emits the YAML. `useCirWriter` gains `writeSignoff()` so this still
  serialises through the single-flight queue alongside text and form
  writes. The agent's only path to flip sign-off remains Write/Edit/Bash
  of `board.cir.yaml` — and `is_cir_write` already routes those through
  the approval gate (SPEC-1 FR-16). The "sign-off is human-only" test
  asserts an agent Write of a signoff-flipping board still gates.
- **Workspace persistence is a small JSON file the backend owns.**
  `~/.config/ki-mcp-pcb/session.json` stores `{"last_workdir": "<abs>"}`.
  `session.working_dir()` consults `KIMP_GUI_WORKDIR` (explicit
  override) first, then `session.json` (persisted choice), then the
  existing default. `GET/POST /api/workspace` reads/writes the file;
  POST validates the path exists and is a directory before persisting.
  No in-process state — the file is the truth (mirrors the CLAUDE.md
  "state lives on disk" rule for the MCP server).
- **KiCanvas preview is the verified shape.** The static viewer at
  `static/app.js:267-277` already loads `kicanvas.js` from the CDN and
  embeds `<kicanvas-embed src="<.kicad_pcb>" controls="full">` — that
  shape is known to render. The React component mirrors it, lazy-loads
  the script once per page, and degrades cleanly if the CDN fetch fails
  (the user sees a "PCB preview needs network access" notice, never a
  silent blank). When a sibling `.kicad_pro` artifact is present, it is
  attached as a `<kicanvas-source>` child for the richer project view.
  Tests don't render KiCanvas itself (jsdom can't) but assert the
  embed's resolved `src` and the lazy-load script tag.

## Tasks

Each task ships with paired tests. The suite stays green task-by-task:
`pytest`, `ruff`, `mypy`, the TS `lint`/`test`/`build`.

### G4-T1 — Backend: `POST /api/parse_intent`

Wraps `parse_nl`; returns `{board, draft_yaml}` on success, 503
`{detail: ...}` when `NLParserUnavailableError` fires (SDK or
`ANTHROPIC_API_KEY` absent), 400 for `NLParserError`/empty prompt.
Tests monkeypatch `server.parse_nl` so CI never calls Anthropic.

### G4-T2 — Backend + Frontend: workspace persistence

Backend: `session.json` reader/writer, `GET/POST /api/workspace`,
`session.working_dir()` consults env > persisted > default. Frontend:
`WorkspacePanel` shows the current path, "Open workspace…" prompts for
an absolute path and posts it; on success the page reloads so every
view re-fetches against the new working directory.

### G4-T3 — Frontend: regen schema + typed client methods

`parseIntent`, `getWorkspace`, `setWorkspace`, plus the G4-T5
`patchSignoff` once that ships. Drift check verified on each gen.

### G4-T4 — Frontend: `IntentDialog`

A modal: prompt textarea → Generate (calls `parseIntent`) → preview the
draft YAML → Accept writes it as the working CIR through
`writer.flush()` + `putCir(text)`. Reject closes the modal. Surfaces
the 503 unavailable state with the "configure ANTHROPIC_API_KEY"
message.

### G4-T5 — Backend `PATCH /api/cir/signoff` + Frontend `SignoffPanel`

Backend: `SignoffPatch` Pydantic model (all fields optional),
`Signoff.model_copy(update=...)`, re-serialize. Frontend: panel that
reads `cirState.board.signoff`, lets a human toggle the four flags and
optionally fill reviewer/reviewed_at, writes through
`useCirWriter.writeSignoff()` (a new method that flushes pending text
first, then PATCHes — single-flight stays intact). The agent gate
already covers any agent-driven CIR write (FR-16); an explicit test
asserts the gate fires when an agent Writes a board.cir.yaml that
flips a sign-off flag.

### G4-T6 — Frontend: `KiCanvasPreview`

Auto-resolves the populated `.kicad_pcb` and optional `.kicad_pro` from
the artifact list, lazy-loads `kicanvas.js` once, renders
`<kicanvas-embed src controls="full">` with the project source. On
script-load failure → an error notice with the offline-degrade message.

### G4-T7 — Frontend: App wiring

`WorkspacePanel` in the header; `IntentDialog` launcher button in the
editor pane's empty state; `SignoffPanel` in the editor pane below
sourcing/BOM; `KiCanvasPreview` in the center pane after the artifact
list. All gated on the right `cirState` / artifact preconditions so the
empty-state UX is clean.

### G4-T8 — End-to-end milestone exit

A full vitest run-through of SPEC-1 G4's exit ("the GUI is a complete
co-pilot; the terminal is genuinely optional"): intent → preview →
accept → working CIR exists → form mode unlocks → sign-off panel shows.
Plus the "agent cannot flip sign-off" backend regression and a live
launcher smoke (boot the unified launcher, hit the new endpoints, tear
down).

## Verification gate

G4 is complete when:
- backend `pytest` (full suite), `ruff`, `mypy --strict` all green;
- TS `lint` / `test` / `build` green, `gen:types` zero drift;
- the intent-to-form-unlock e2e (T8) passes;
- a live launcher smoke shows `parse_intent` returning 503 (no key in
  CI) and the workspace endpoints round-tripping cleanly.

## Risks

| Risk | Mitigation |
|---|---|
| Anthropic API key absent on the user's machine | 503 with the structured "no key" detail; the IntentDialog renders an actionable message. Pipeline + form panes keep working. |
| Agent flips sign-off through Bash | `is_cir_write` already covers `Bash` commands referencing the CIR file (G2 robustness extension); regression test in T8. |
| KiCanvas CDN offline / blocked | Script-load failure → user-facing notice; the rest of the GUI keeps working. |
| Workspace persistence corrupts | `POST /api/workspace` validates the path; malformed `session.json` is treated as absent (fall through to default). |
| Path-traversal in `POST /api/workspace` | Reject relative paths and paths that don't resolve to a real directory; never write outside the validated target. |

## Status — completed 2026-05-20

All eight tasks (G4-T1 … G4-T8) and their paired test todos are done. The
verification gate is green: backend `pytest` (432 passed, 2 skipped — the
live LLM evals), `ruff`, `mypy --strict` (66 source files); frontend
`npm run lint` / `test` (149 passed) / `build`.

Notable resolutions beyond the original sketch:

- **Sign-off via focused PATCH, not a full-board write.** A new
  `PATCH /api/cir/signoff` applies a partial `Signoff` via
  `model_copy(update=...)` and re-emits the canonical YAML. The frontend
  writer gained a `writeSignoff(patch)` that goes through the same
  single-flight queue as text and form writes — so flipping a sign-off
  flag never re-serialises the rest of the board and never clobbers a
  text autosave.
- **Workspace persistence is on-disk, atomic, env-override-friendly.**
  `~/.config/ki-mcp-pcb/session.json` (overridable via
  `KIMP_GUI_SESSION_FILE` for tests). Written through a `tmp + rename`
  so a crash mid-write cannot corrupt the file. `working_dir()` resolves
  env > persisted > default; `working_dir_source()` lets the GUI render
  the right control state.
- **KiCanvas shape verified before wiring.** The bare-`src` form
  matched the legacy static viewer; the richer project view uses
  `<kicanvas-source>` when a `.kicad_pro` artifact is present.
  Script-load failure surfaces an offline-degrade notice (no silent
  blank).
- **Agent gate still owns sign-off enforcement.** The existing
  `is_cir_write` route already covers any Write / Edit / Bash that
  touches `board.cir.yaml` — including content that flips a
  `signoff.*` flag — so the spec's "an LLM cannot flip sign-off" is
  preserved. A regression test asserts this.

Live-verified end-to-end against the unified launcher:

- `GET /api/workspace` reports the env-override correctly.
- `POST /api/parse_intent` returns 503 with the structured "anthropic
  not installed" detail (the documented degrade path).
- `PUT /api/cir` seeds the working CIR.
- `PATCH /api/cir/signoff { rf_reviewed: true, reviewer: "smoke-test" }`
  returns the updated `CirState` with `rf_reviewed=True` and the
  reviewer persisted to disk.

**G4 closes SPEC-1.** All FR-1 … FR-17 surfaces are now built and tested:
the terminal is genuinely optional — a user can boot the launcher,
describe a board in plain English, watch the agent build it, review
sign-off in the GUI, preview the populated PCB in KiCanvas, and switch
working directories without re-launching.
