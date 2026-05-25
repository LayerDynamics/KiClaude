---
name: snapshot
description: Create a named, revertable snapshot of the current KCIR project state via kc_snapshot_create, so a later /revert can roll back to exactly this point. Read-only with respect to the .kicad_* files — snapshots live in the content-addressed store, not the board.
argument-hint: "[message]   a human label for the snapshot; defaults to a timestamp"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_snapshot_create
---

# /snapshot — pin a revertable checkpoint

A snapshot is a cheap "save point" before a risky edit (a big
reroute, a chat-driven refactor, a `/route-freerouting` pass). It does
NOT touch the `.kicad_*` files — it records the current KCIR so
`/revert <id>` can restore it.

## Flow

1. **Resolve the label** — use `$ARGUMENTS` as the snapshot message; if
   empty, synthesise `snapshot @ <UTC timestamp>`.
2. **Create** — call `kc_snapshot_create` with `{project_id, label}`.
   It returns `{ok, snapshot_id, ts}`.
3. **Report** — surface the `snapshot_id` and label so the user can
   feed it to `/revert` later. Don't save the project (snapshots are
   independent of disk writes).

## Notes

- Snapshots are KCIR-level (SPEC §7.4 / D7) — the canonical persistent
  form is still the KiCad files on disk; this is for the time-travel UI.
- Take a snapshot automatically before any destructive multi-step
  command. The M1-P-06 PreToolUse gate already auto-snapshots before
  single mutating tool calls; `/snapshot` is the explicit, user-named
  version for a whole session of work.
