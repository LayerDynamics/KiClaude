---
name: explore-placements
description: Spawn the M3-P-07 placement-explorer subagent with N candidate seed placements for the board's footprints, return ranked variants by total track-length × clearance-headroom. Useful when initial placement feels random and the user wants empirical evidence for the layout choice.
argument-hint: "[--seeds N] [--metric track-length|clearance|combined]   defaults: seeds=8 metric=combined"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
---

# /explore-placements — empirical placement A/B/N

Manual placement is a guessing game. Subagent-driven exploration
turns that into "try N seeds, rank them, pick the winner". The
command does not commit any change — it produces a ranked report
of candidate variants that the user can adopt with `kc_snapshot_revert`
on the chosen seed's snapshot id.

## Flow

1. **Snapshot the current placement** as the baseline so the user
   can always come back.
2. **Spawn `placement-explorer`** (M3-P-07 subagent) with the
   project, the chosen `--seeds` count, and the optimisation metric.
   The subagent fans out N parallel internal trials, each
   committing to a snapshot named `placement_seed_K`.
3. **Score each seed** by:
   - `track-length` — sum of net-by-net Manhattan distances
     between connected pads (no routing, just geometry).
   - `clearance` — minimum pad-to-pad clearance across the board
     (proxy for routing-headroom — bigger = easier to route).
   - `combined` — geometric mean of normalized track-length and
     clearance scores (default).
4. **Rank** seeds by score; surface top 3 with snapshot ids so the
   user can revert into any of them with one command.

## Output shape

```text
EXPLORE-PLACEMENTS seeds=8 metric=combined
  baseline: score=0.612  total_length=842mm  min_clearance=0.31mm
  seed_03:  score=0.871  total_length=611mm  min_clearance=0.42mm   ← top
  seed_07:  score=0.853  total_length=634mm  min_clearance=0.39mm
  seed_01:  score=0.819  total_length=658mm  min_clearance=0.41mm
  (...5 more...)

Top seed: placement_seed_03 — `/snapshot revert placement_seed_03`
```

## Anti-patterns

- **Don't commit the winning seed automatically.** The user's
  intuition often beats the metric (silk layout, mechanical
  constraints, vendor part-rotation requirements). Always require
  an explicit `kc_snapshot_revert` to adopt.
- **Don't run with `--seeds > 32`.** The subagent's parallel
  trials all hold a project copy in memory; 32+ pegs kiserver.
  If the user needs more diversity, run twice with different
  seed-RNG sources rather than one massive sweep.
