---
name: design-review
description: Audit a 4-layer M3-era PCB for stackup correctness, controlled-impedance discipline, diff-pair routing health, length-match group status, and decoupling coverage. Use when the user asks for a `/pcb-review`, a "design audit", or wants a second opinion on a board before fab.
allowed-tools:
  - mcp__kiclaude__kc_project_open
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_netclass_list
  - mcp__kiclaude__kc_impedance
  - mcp__kiclaude__kc_diffpair_declare
  - mcp__kiclaude__kc_length_match
  - mcp__kiclaude__kc_validate
---

# design-review — audit a 4-layer high-speed PCB

A design review at M3 is **structured**: each pass below produces a
specific class of finding, scored as `error` (blocks fab),
`warning` (advisory), or `info` (style/clarity). The review ends
with a single-line verdict suitable for paste-back into chat.

## Order of operations

Run these passes in order. Findings from later passes often depend
on earlier passes succeeding (you can't meaningfully audit
controlled-impedance traces if the stackup itself is wrong).

### 1. Stackup correctness

Read `project.stackup` and verify:

- Layer order is physically realisable — copper / dielectric /
  copper alternation, no two coppers adjacent, no missing
  dielectric between copper pairs. Cross-check the layer count
  against `pcb.layers` (should be `2N` copper for an N-pair
  stackup).
- Total `board_thickness_mm` ≈ sum of layer thicknesses (±0.1 mm
  manufacturing tolerance). Mismatches usually mean the user
  forgot to update one side after editing the other.
- `controlled_impedance` flag set when any net class declares a
  `diff_pair_width_mm` or any `pcb.diff_pairs[]` entry exists.
  An impedance-controlled board with the flag off is a fab
  request the operator may silently drop.

### 2. Net-class clearance vs fab rules

Walk `pcb.net_classes` and verify each `clearance_mm` is ≥ the
**lowest-tier fab's** minimum (the M2-Q-03 DFM module exposes
those minima per target). A board with a 0.1 mm clearance class
that ships to an OSHPark order (0.1524 mm minimum) gets rejected
at the CAM stage.

### 3. Diff-pair routing health

For each `pcb.diff_pairs[]` entry:

- Both legs are routed (use `kc_length_match` to confirm
  neither carries `Unrouted` status in a sibling length group).
- Per-leg trace width matches the impedance solver's
  recommendation for the declared `target_impedance_ohms` on the
  current stackup. Tolerance: ±2 Ω. Mismatches surface as
  `warning`.
- Leg-to-leg `skew_tolerance_mm` is honoured — call
  `kc_length_match` against the pair's implicit two-net group
  and verify the delta is within tolerance.

### 4. Length-match group status

Call `kc_length_match` for the whole PCB. Any group with even one
`TooLong` member is an `error` — there's no auto-fix path, so
the user has to re-route. `TooShort` members are `warning` (the
M3-T-04 panel proposes serpentine tuning).

### 5. Decoupling coverage

For every IC footprint, walk the footprint's pads and verify
every power-input pad has a decoupling capacitor within
`MAX_CAP_DISTANCE_MM` (default 2 mm) on the same net. Missing
caps are `warning` unless the IC is a regulator or MCU with a
declared `VDD_CORE` pin, in which case they're `error`.

### 6. DRC sanity

Always end with `kc_drc` against the live PCB. Any `error`
severity returned is a hard blocker regardless of what the
review-specific passes found.

## Verdict format

After all six passes, produce one of:

- `READY-TO-FAB` — zero errors, zero warnings across all passes.
- `READY-TO-FAB WITH N WARNINGS` — zero errors, N warnings.
  Caller decides whether to ship anyway.
- `BLOCKED — N errors` — at least one error finding. Caller
  must fix before fab.

Always include a one-line summary of WHICH findings are blocking,
e.g.: `BLOCKED — 2 errors: net-class POWER clearance 0.1 mm vs OSHPark 0.1524 mm; DDR_BYTE0/DQ2 TooLong by 0.8 mm`.

## Anti-patterns

- **Don't run the review against an unsaved project.** The
  KCIR snapshot the tools read is the on-disk state; mid-edit
  in-memory state may not match. If `kc_validate` reports the
  project is dirty, ask the user to save first.
- **Don't recommend changes the user didn't ask for.** A
  review's job is to surface findings, not refactor. If a track
  is sub-optimal but DRC-clean and within net-class spec, it's
  `info` — not a finding the user has to act on.
- **Don't fold M2 DFM into the review.** M2-Q-03 already runs at
  export time. A design review is the *pre-export* audit; the
  DFM dry-run is the export-time gate.
