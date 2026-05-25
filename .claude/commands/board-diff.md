---
name: board-diff
description: Diff the working-tree board against a Git ref — a structured (textual) delta of footprints / tracks / vias / zones / nets via kc_diff, plus the visual SVG diff from the `kiclaude diff` CLI. Read-only; produces a PR-friendly summary (FR-006, FR-073).
argument-hint: "<git-ref>   e.g. HEAD, main, a tag, or a commit SHA to compare the current .kicad_pcb against"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_diff
  - Bash
---

# /board-diff — compare the board against a Git ref

Answers "what changed on this board since `<ref>`?" for review, exactly
like reading a code diff before merging.

## Flow

1. **Require a ref** — `$ARGUMENTS` is the Git ref to compare against
   (default `HEAD` if the user clearly means "since my last commit", but
   confirm rather than guessing for an ambiguous request).
2. **Materialise the ref's board** — the current `.kicad_pcb` is on
   disk; extract the ref's version to a temp path:
   `git show <ref>:<path-to>.kicad_pcb > /tmp/board-<ref>.kicad_pcb`.
   If the file didn't exist at that ref, say so (it's a newly-added
   board — everything is "added").
3. **Structured diff** — `kc_diff` with the two board paths/projects
   returns the per-collection delta: footprints / tracks / vias / zones
   / nets `added | removed | modified`. Summarise it as a table.
4. **Visual diff** — run `kiclaude diff <ref-board> <current-board>`
   (the M2-T-11 CLI) for the side-by-side SVG; surface the output path.
5. **Report** — lead with the headline ("3 footprints moved, 1 net
   added, 12 track segments changed"), then the table, then the SVG
   path. Never mutate anything — this command is strictly read-only.

## Notes

- This is the board analogue of `git diff`. It's the review surface the
  spec promises (FR-006 project diff view, FR-073 PR-friendly diff).
- For schematic-side diffs, `git diff` on the `.kicad_sch` is usually
  enough since the emitter is deterministic; `/board-diff` exists
  because PCB S-expressions are reordered enough that a structured
  delta is far more readable than a raw text diff.
