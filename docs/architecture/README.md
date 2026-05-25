# kiclaude architecture

This directory holds architecture notes and diagrams. Decision records
live in [`../ADR/`](../ADR/). The authoritative design is
[`../specs/SPEC-01-kiclaude.md`](../specs/SPEC-01-kiclaude.md); this is a
navigable overview of how the running system is wired today.

## One-line identity

Browser-native, AI-native, KiCad-compatible EDA — Claude Code's hardware
counterpart. A user opens a `.kicad_pro` in the browser, chats with
Claude, and **every edit is a typed MCP tool call** that round-trips back
to the on-disk `.kicad_sch` / `.kicad_pcb` files, reviewable in a Git
diff.

## Layered system (composition order)

```
Browser (client/, React 19 + Vite)
  │  WebSocket (chat stream + tool events) + HTTPS REST
  ▼
services/server   (TS / Hono gateway :8080)  ── catch-all proxy /api/{agent,server,connector}/*
  ├── services/agent      (Py, Claude Agent SDK :8082)  ── hooks: PreToolUse / PostToolUse / Session*
  │     └── services/mcp   (Py, in-process MCP tools)    ── kc_* (Claude-facing) + ui_* (UI-only)
  ├── services/kiserver    (Py, FastAPI :8083)           ── PyO3 → crates/ki; project open/save, BOM, DFM, sync, share
  └── services/kiconnector (Py, FastAPI :8084)           ── subprocess broker: kicad-cli, freerouting, kikit
crates/ki   (Rust)  ── KCIR + .kicad_* parse/emit; built to wasm (browser) AND PyO3 (kiserver)
crates/cad  (Rust)  ── geometry, R-tree, DRC kernel, routers, impedance solver, zone fill; wasm
```

## Load-bearing contracts (first principles, SPEC §1.4)

1. **KiCad files are the contract.** Persistent state is `.kicad_*` on
   disk; round-trip fidelity is a CI gate (`crates/ki/tests/golden.rs`,
   `tests/golden/`).
2. **KCIR is the in-memory contract.** Every transformation passes
   through `crates/ki/src/kcir/`; TypeScript mirrors are ts-rs-generated
   into `client/src/lib/kcir/`. Versioned (`KCIR_VERSION`) with explicit
   migrations under `kcir/migrations/`.
3. **Claude operates through typed tools.** Two disjoint registries:
   declarative `kc_*` tools (Claude-facing, `services/mcp/.../tools/`)
   and coordinate `ui_*` tools (frontend-only, `.../ui_tools/`). A
   boot-time assertion (`server.py::assert_no_ui_tools_in_claude_registry`)
   keeps them apart.
4. **Local-first, cloud-optional.** Runs fully on a user's machine; cloud
   sync (FR-007) + share links (FR-080) are opt-in and content-addressed
   (`kiserver/object_store.py`, env-selected LocalFs vs S3); multiplayer
   (FR-081) is off by default (`KICLAUDE_MULTIPLAYER`).

## Where things live

| Concern | Path |
|---|---|
| KCIR types + migrations | `crates/ki/src/kcir/` |
| `.kicad_*` parse/emit (round-trip) | `crates/ki/src/format/v9/` |
| DRC / routing / impedance / zones | `crates/cad/src/` |
| Claude-facing MCP tools | `services/mcp/src/kc_mcp/tools/` |
| UI-only (coordinate) tools | `services/mcp/src/kc_mcp/ui_tools/` |
| Permission gate (incl. signoff guard) | `services/agent/src/agent/hooks/permission.py` |
| Subagents (decoupling/bom/placement) | `services/agent/src/agent/subagents/` |
| Gateway proxy + CRDT relay | `services/server/src/{proxy,ws,crdt}.ts` |
| Reference projects | `examples/` |
| Decision records | `docs/ADR/` |
| Observability preset | `docs/observability/` |

## Decision records

- [ADR-0001 — CRDT engine: Yjs](../ADR/0001-crdt-yjs.md) (resolves SPEC §16.2 P4).
