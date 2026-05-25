---
name: revert
description: Roll the KCIR project back to a previously recorded snapshot via kc_snapshot_revert, then save so the .kicad_* files on disk reflect the restored state. Pairs with /snapshot.
argument-hint: "<snapshot_id>   the id returned by /snapshot (or shown in the activity journal)"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /revert — restore a snapshot

Undo a session's worth of edits by jumping back to a checkpoint made
with `/snapshot` (or an auto-snapshot from the activity journal).

## Flow

1. **Require an id** — `$ARGUMENTS` must name a `snapshot_id`. If the
   user didn't pass one, ask which snapshot (don't guess); the activity
   journal lists them with their labels + timestamps.
2. **Confirm intent** — reverting discards every change made *after*
   that snapshot. State plainly what will be lost (the user is about to
   overwrite working state) before proceeding.
3. **Revert** — `kc_snapshot_revert` with `{project_id, snapshot_id}`
   restores the in-memory KCIR to the snapshot.
4. **Save** — `kc_project_save` writes the restored KCIR back to the
   `.kicad_*` files so disk matches. Surface the files written.

## Notes

- This is a hard rollback of KCIR state; it is itself recorded, so a
  mistaken revert can be undone by reverting to the auto-snapshot the
  gate takes beforehand.
- If the `snapshot_id` is unknown, `kc_snapshot_revert` returns
  `{ok:false}` — report it and list the valid ids rather than failing
  silently.
