# CLAUDE.md — guidance for Claude Code working on kiclaude

## What kiclaude is

**kiclaude is browser-native, AI-native, KiCad-compatible EDA — Claude
Code's hardware counterpart.** A user opens a `.kicad_pro` project in
the browser (or via a local daemon), chats with Claude about the
circuit, and Claude proposes edits as typed MCP tool calls that
round-trip back to the on-disk `.kicad_sch` / `.kicad_pcb` files. Every
mutation is reviewable in a Git diff.

**Authoritative documents:**

- [`docs/specs/SPEC-01-kiclaude.md`](../docs/specs/SPEC-01-kiclaude.md) — the spec. If anything here conflicts with the spec, the spec wins.
- [`docs/plans/2026-05-21-kiclaude-m0-m3.md`](../docs/plans/2026-05-21-kiclaude-m0-m3.md) — the M0–M3 implementation plan.

## First principles (do NOT violate)

Mirrored from spec §1.4:

1. **KiCad file format is the contract.** Persistent state lives in `.kicad_sch` / `.kicad_pcb` / `.kicad_pro`. Round-trip fidelity is a CI gate (`M0-Q-02`).
2. **KCIR is the in-memory contract.** Every transformation passes through the kiclaude Canonical Intermediate Representation in `crates/ki/src/kcir/`. TypeScript + JSON Schema mirrors are generated from it.
3. **Claude operates through typed tools, never free-form file edits.** No raw `Write` access to `.kicad_pcb`. Every Claude-initiated mutation is a structured MCP tool call.
4. **Claude reasons in declarative hints; raw coordinates are UI-only.** Tools like `kc_footprint_place_xy` are exposed only to the React frontend (drag-drop, property panel). Claude-facing tools are declarative: `kc_footprint_place_hint(refdes, constraints=["near MCU", "south edge"])`. The MCP registry enforces this split (see spec §A.2).
5. **Every MPN must resolve.** Synthesis fails closed if a part can't be found in a real distributor's stock list. No hallucinated parts.
6. **MCP tools are stateless.** State lives in files (browser FS Access handle or `kiconnector` daemon path). Tools take paths and args in, return structured JSON out.
7. **No free-form prose from MCP tools.** Structured JSON only — Claude narrates on top.
8. **Local-first, cloud-optional.** Must run entirely on a user's machine (browser + local `kiconnector`) with no cloud account beyond the Anthropic API key.

## Repository layout

```
crates/
  ki/              Rust: KCIR, .kicad_* parsers/emitters, PyO3 + wasm-bindgen
  cad/             Rust: geometry primitives, R-tree, DRC kernel
services/
  agent/           Python: FastAPI + ClaudeAgentSDK driver (:8082)
  mcp/             Python: in-process MCP server with kc_* tools
  kiserver/        Python: FastAPI + PyO3-loaded ki_native (:8083)
  kiconnector/     Python: local subprocess broker for kicad-cli (:8084)
  server/          TypeScript: Hono WS + REST gateway (:8080)
client/            React + Vite + Tailwind + Radix + Zustand (browser EDA UI)
packages/
  cli/             TypeScript: `kiclaude` CLI (`kiclaude mcp stdio`, etc.)
  kithree/         TypeScript: three.js board viewer
examples/
  blinky/          M0 reference project (.kicad_pro + .kicad_pcb + lib tables)
tests/
  e2e/             Playwright cross-service smoke (Q-03)
  golden/          Round-trip golden-file gates (Q-02)
docs/
  specs/SPEC-01-kiclaude.md
  plans/2026-05-21-kiclaude-m0-m3.md
.claude/
  settings.json    Registers the kiclaude MCP server
  CLAUDE.md        (this file)
```

## How to run things

- **Rust:** `cargo test --workspace`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo fmt --check --all`.
- **Wasm:** `wasm-pack build --target web crates/ki` (and `crates/cad`).
- **Python:** `uv sync --all-extras`, `uv run ruff check services/`, `uv run mypy --strict`, `uv run pytest`.
- **Node:** `pnpm install`, `pnpm -r typecheck`, `pnpm -r test`, `pnpm -F client dev`.
- **E2E:** `pnpm -F e2e test` (Playwright; gated on `ANTHROPIC_API_KEY`).
- **CLI:** `kiclaude --version`, `kiclaude mcp stdio`, `kiclaude validate <project>`.

## When you (Claude) make changes

1. **Check the spec first.** If a request affects KCIR shape, file emit, MCP tool surface, or first-principles compliance — read `docs/specs/SPEC-01-kiclaude.md` before writing code.
2. **Schema-first.** KCIR field changes require a `kcir::migrations` entry and a `kcir_version` bump. Never silently change a field's meaning.
3. **Round-trip-or-die.** Any change to `crates/ki/src/format/v9/` runs against `tests/golden/round_trip.rs` (M0-Q-02). Failure blocks merge.
4. **Add MCP tools through the typed registry only** (`services/mcp/src/kc_mcp/tools/`). Document which tool set the new tool belongs to (Claude-facing declarative vs. frontend-only coordinate tools).
5. **Update the plan checkbox** when you finish a task: `docs/plans/2026-05-21-kiclaude-m0-m3.md`.

## Things that look helpful but aren't

- **Don't bypass round-trip gates** because the output "looks fine". If `M0-Q-02` fails, the change is wrong — even if KiCad opens the file.
- **Don't expose new raw-coordinate tools to Claude.** That's a violation of first principle #4; pair every coordinate tool with a declarative-hint twin.
- **Don't hallucinate MPNs.** If you need a part, query a real distributor stock list (Digikey / Mouser / LCSC) via the existing MPN resolver; fail-closed.
- **Don't add a second EDA backend before KiCad is solid.** A `Backend` abstraction may exist; resist using it until post-M3.

## When you're unsure

Ask. The user prefers a clarifying question over a wrong assumption that ripples through KCIR or the file emitters.
