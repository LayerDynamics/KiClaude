# Implementation Plan — GUI Co-pilot, Milestone G3

| | |
|---|---|
| **Date** | 2026-05-19 |
| **Implements** | [`SPEC-1`](../specs/SPEC-1-gui-copilot.md) — milestone **G3: Structured form editor + full results** |
| **Builds on** | G2 ([`2026-05-19-gui-copilot-g2.md`](./2026-05-19-gui-copilot-g2.md)) — complete |
| **Packages** | `ki_mcp_pcb_web` (backend), `ki_mcp_pcb_gui` (frontend) |

## Goal

Three CIR authoring modes — text, form, chat — all edit the same on-disk
`board.cir.yaml` and stay in sync (FR-3, FR-4). The pipeline-results pane
gains the views the backend already supplies: BOM, structured diff,
per-net impedance, decoupling coverage, return-path check (FR-7, FR-10,
FR-11).

## Architecture (resolved)

- **Form → CIR is server-authored.** A new `PUT /api/cir/board` takes a
  validated `Board` JSON, serialises it through `yaml.safe_dump(board.
  model_dump(mode="json"), sort_keys=False)`, persists it, and returns the
  same `CirState` shape `PUT /api/cir` returns. The round-trip
  `parse_yaml → model_dump → yaml.safe_dump → parse_yaml == Board` holds
  across the four example boards (verified before locking the design); the
  Pydantic CIR stays the single source of truth and TS never owns YAML.
- **Form ↔ text concurrency is single-flight.** A `useCirWriter` hook in
  `App.tsx` serialises every CIR write (text autosave + form save) through
  one in-flight queue. While a write is in flight, the form's Save button
  and the text editor are both visually "saving"; new edits enqueue. This
  closes the gap where a form save could overwrite a typed-but-unsaved
  buffer (and vice versa).
- **Stackup form does real layer editing.** Add/remove/reorder/edit the
  `Stackup.layers` list with full coverage of `kind`, `thickness_mm`,
  `material`, `er`, so form mode is not strictly less capable than text mode.
- **Result views consume backend endpoints, not duplicate logic.** Diff
  view calls a new `POST /api/diff/working` (one baseline upload diffed
  against the working CIR). Decoupling and return-path checks get new
  `POST /api/decoupling_check` / `/api/return_path_check` endpoints that
  wrap the existing core helpers. Impedance and BOM already flow through
  `POST /api/impedance` / `CirState.bom`.

## Tasks

Each ships with paired tests, in the G1/G2 cadence; the suite stays green
task-by-task (`pytest`, `ruff`, `mypy`, the TS `lint`/`test`/`build`).

### G3-T1 — Backend: `PUT /api/cir/board`

New endpoint: accepts a `Board`-shaped JSON body, validates via Pydantic,
emits canonical YAML, writes to `session.cir_path()`, returns `CirState`.
Reuses the `_cir_state` shape so the GUI's parsed/validated/bom/sourcing
pipeline is unchanged.

### G3-T2 — Backend: decoupling / return-path / diff-vs-working

`POST /api/decoupling_check` and `POST /api/return_path_check` wrap
`cir.validation._check_decoupling_coverage` and `_check_return_paths` and
return `{issues: [...]}`. `POST /api/diff/working` takes one baseline
upload and diffs against the working CIR, returning the same shape
`POST /api/diff` does.

### G3-T3 — Frontend: regen `schema.ts` + typed client methods

`npm run gen:types` to refresh `src/api/schema.ts` with the new
endpoints, then add `putCirBoard`, `decouplingCheck`, `returnPathCheck`,
`diffAgainstWorking` to `src/api/client.ts` with the right types.

### G3-T4 — Frontend: ComponentsForm + NetsForm

`src/form/ComponentsForm.tsx` — table-style editor over `Board.components`:
`refdes`, `mpn`, `value`, `partition` (enum), `decoupling_pins`,
`bga_pitch_mm`, plus add/remove rows. `src/form/NetsForm.tsx` — same shape
over `Board.nets` covering the M2-M4 fields actually wired through the
pipeline (`net_class`, `members`, `power_rail`, `length_match_group`,
`target_impedance_ohm`, `diff_pair_with`, `cpwg_gap_mm`, `topology`,
`fly_by_order`, `trace_width_mm`/`spacing_mm`, `reference_plane`).

### G3-T5 — Frontend: StackupForm (with layers) + FabForm

`src/form/StackupForm.tsx` — list editor for `Stackup.layers` (add /
remove / move-up / move-down, with `kind` enum, `name`, `thickness_mm`,
`material`, `er`), plus `finished_thickness_mm`, `controlled_impedance`,
`power_plane_layers`. `src/form/FabForm.tsx` — the five fab fields
(`name` enum, `min_trace_mm`, `min_space_mm`, `min_drill_mm`,
`min_annular_ring_mm`, `layer_count`).

### G3-T6 — Frontend: `BoardForm` shell + `useCirWriter` + App wiring

`src/form/BoardForm.tsx` collects the four sub-forms in a tabbed/section
layout. `src/cir/useCirWriter.ts` is the single-flight writer: it owns
the in-flight state and exposes `writeText` / `writeBoard` to both
CirEditor and BoardForm. `App.tsx` switches the left pane to a Text |
Form tab and hands the writer down. A form save bumps `cirReload` so the
text view picks up the canonical YAML.

### G3-T7 — Frontend: Diff / Impedance / Decoupling / Return-path views

Four `src/results/` views, each consuming its endpoint and rendering the
structured result. DiffView lets the user pick a baseline file and shows
added/removed/changed components + nets. The other three are read-only
panels driven by the current working CIR. All wire into the center pane.

### G3-T8 — Frontend: BomView + final wiring

`src/results/BomView.tsx` renders `CirState.bom` (already exposed) as a
table. App.tsx wires the BOM and the four new result views into the
center pane; T6's tabs ship behind one consistent interaction model.

## Verification gate

G3 is complete when:
- backend `pytest`, `ruff`, `mypy` stay green;
- the TS `lint` / `test` / `build` stay green and `gen:types` has zero drift;
- a `form-edit → text-reload → identical YAML` integration test passes;
- the `useCirWriter` single-flight queue is covered by a concurrent-edit test.

## Risks

| Risk | Mitigation |
|---|---|
| Canonical YAML loses user-formatted comments / ordering | Documented — the file is the canonical artifact; users who curate formatting use text mode. Round-trip equality on all four example boards verified. |
| Form vs text concurrent edits | `useCirWriter` single-flight queue; UI shows in-flight state. |
| Form coverage gaps (stackup layers, complex nets) | Stackup layer editor is in scope; rare Net fields exposed only where the M2-M4 pipeline reads them. The text editor remains the escape hatch for anything the form doesn't surface. |
| Result-view UI rendering many rows | Plain HTML tables; no virtualization until a real board hits the limit. |
| TS schema drift in CI | `gen:types` runs in CI with `git diff --exit-code`. |

## Status — completed 2026-05-20

All eight tasks (G3-T1 … G3-T8) and their paired test todos are done. The
verification gate is green: backend `pytest` (410 passed, 2 skipped — the
live LLM evals), `ruff`, `mypy --strict` (63 source files); frontend
`npm run lint` / `test` (113 passed) / `build`.

Architectural decisions worth keeping:
- The form editor writes a **structured Board** to a new
  `PUT /api/cir/board`, which `yaml.safe_dump(board.model_dump(mode="json"))`s
  the canonical YAML. Round-trip `parse → dump → parse == Board` verified
  on all four example CIRs before the design was locked.
- A single-flight `useCirWriter` hook serialises text autosave + form save
  through one in-flight queue, with `flush()` ensuring form saves drain
  pending text first — so the two authoring modes can never clobber each
  other's edits.
- Result endpoints (`/api/decoupling_check`, `/api/return_path_check`,
  `/api/impedance/working`, `/api/diff/working`) all return typed Pydantic
  models the frontend imports via the OpenAPI-generated `schema.ts`.
- The stackup form supports full add/remove/reorder/edit of layers so form
  mode is genuinely as capable as text mode (FR-4).

Live-verified end-to-end: launched `kimp serve`, PUT a structured Board,
GET each check endpoint, observed the expected 400/ok/issue shape.

Next milestone: **G4** (new-project-from-intent, sign-off surfaces,
session persistence, KiCanvas PCB preview).
