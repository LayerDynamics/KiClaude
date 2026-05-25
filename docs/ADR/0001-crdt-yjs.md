# ADR 0001 — CRDT engine for multiplayer: Yjs

**Status:** Accepted — 2026-05-25
**Resolves:** SPEC-01 §16.2 pending decision **P4** ("CRDT vendor (if M5
multiplayer): Yjs vs Automerge vs custom?")
**Relates to:** FR-081 (real-time multiplayer editing), §15 risk
"Concurrent edits collide (no CRDT in v1)".

## Context

FR-081 makes single-editor + Git the v1 merge story and defers
real-time multiplayer to M5, "CRDT-backed". SPEC §16.2 left the CRDT
vendor open (P4, due at M5 kickoff). M5 work now needs a concrete engine
to build the multiplayer sync layer against.

The candidates:

| Option | Notes |
|---|---|
| **Yjs** | Mature, widely deployed, small core, binary update protocol, framework-agnostic, MIT. Rich provider ecosystem (`y-websocket`, awareness, IndexedDB persistence). |
| **Automerge** | Strong JSON-document model + history; heavier wasm core; Rust-native (aligns with our crates) but a larger client bundle and a younger JS sync story. |
| **Custom** | Full control; multi-month effort to get convergence + GC right; rejected by the same reasoning as the DRC/offset "don't reinvent" calls. |

## Decision

**Use Yjs.** Rationale:

1. **Smallest correct path to convergence.** Yjs's `Y.Doc` +
   `encodeStateAsUpdate` / `applyUpdate` / `encodeStateVector` give a
   stable, documented update-based sync we can drive over our existing
   `@hono/node-ws` gateway without adopting a heavyweight server.
2. **License fit.** MIT — clean under SPEC §13 gate #7 / NFR-009.
3. **Framework-agnostic.** No React/Rust coupling; the client binds a
   `Y.Map` to the Zustand store, the server keeps an authoritative
   `Y.Doc` per project room.
4. **Local-first compatible (FP#8).** Multiplayer is **off by default**
   (a per-deployment / per-project feature flag). With it off, the
   single-editor + Git story is unchanged.

## Scope (M5 v1)

- A single `Y.Map` mirrors the project JSON. This gives real,
  conflict-free concurrent editing of the shared document.
- **Semantic / KCIR-aware CRDT** (typed merges of footprints, tracks,
  nets with domain invariants) is **explicitly post-v1**. The v1 layer
  converges at the JSON level; the KiCad files on disk remain the
  contract (FP#1), and a converged document still round-trips through
  the emitter.
- Server is a relay with an authoritative per-room `Y.Doc`; awareness
  (cursors/presence) can layer on later via `y-protocols/awareness`.

## Consequences

- Adds `yjs` to `services/server` and `client` (MIT, small).
- The sync transport is a thin update-relay over WebSocket
  (`services/server/src/crdt.ts`, `client/src/lib/crdt.ts`) rather than
  the full `y-websocket` server, avoiding a `ws`-vs-`@hono/node-ws`
  adapter while staying protocol-compatible with Yjs updates.
- If Automerge's Rust-native model later becomes compelling for a
  KCIR-aware CRDT, this ADR is superseded by a new one; the JSON-level
  Yjs layer is isolated behind the feature flag and the `crdt.*`
  modules.
