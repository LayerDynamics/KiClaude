---
name: diffpair
description: Declare a differential pair from two selected nets, run the M3-R-02 impedance solver to pick widths that hit the target Zdiff, and (optionally) route the pair with the M3-R-04 diff-pair router. User approves each step through the M1-P-06 PreToolUse gate.
argument-hint: "<pos_net> <neg_net> [--zdiff <ohms>] [--gap-mm <mm>] [--length-group <name>]   defaults: Zdiff=90 (USB-class), gap=0.127 mm"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_impedance
  - mcp__kiclaude__kc_diffpair_declare
  - mcp__kiclaude__kc_diffpair_route
  - mcp__kiclaude__kc_netclass_set
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_project_save
---

# /diffpair — declare and (optionally) route a differential pair

Declaring the pair is the design step; routing is optional. The
command always declares; it only routes if the user passes
`--route` or both legs already have trace endpoints to chase.

---

## Flow

1. **Read stackup + net info** — `kc_kcir_get` returns
   `project.stackup` and the two nets so the solver can compute
   recommended widths for the chosen `Zdiff`.

2. **Solve** — `kc_impedance` runs
   `find_diff_microstrip_widths_for_zdiff(zdiff, gap, h, er, t)`
   against the active signal layer. Returns a recommended
   per-trace width. Always surface the result before declaring so
   the user can sanity-check it against the fab's minimum trace
   width.

3. **Snapshot** — `kc_snapshot_create` so a declaration that the
   user later regrets is one-click revertable.

4. **Declare** — `kc_diffpair_declare` writes the new entry into
   `pcb.diff_pairs[]`: name, positive net, negative net, target
   impedance, gap, optional length group, default skew tolerance
   (0.127 mm = 5 mil — the M3 SI rule of thumb).

5. **Set the net class** — `kc_netclass_set` updates the
   `diff_pair_width_mm` and `diff_pair_gap_mm` on whichever net
   class both nets belong to (or creates a new one if they don't
   share). This lets the M3-T-05 push-and-shove tool fall back to
   the right widths if the user re-routes by hand.

6. **(Optional) route** — when `--route` is passed, run
   `kc_diffpair_route` to lay both legs in lockstep with the
   declared gap. The router gates on the M1-P-06 PreToolUse
   approval. Skip if the pair already has tracks (don't stomp
   user routing).

7. **DRC** — `kc_drc` after any track change. Surface violations
   as a pre-save block.

8. **Save** — `kc_project_save` after DRC is clean.

---

## Defaults that match common buses

| Bus              | --zdiff | --gap-mm |
| ---------------- | ------- | -------- |
| USB 2.0          | 90      | 0.127    |
| USB 3.x SS pair  | 90      | 0.127    |
| 100BASE-T MDIO   | 100     | 0.200    |
| Gigabit MDIO     | 100     | 0.200    |
| LVDS             | 100     | 0.150    |
| SATA             | 100     | 0.150    |
| PCIe Gen 1/2     | 85      | 0.127    |
| HDMI TMDS        | 100     | 0.127    |

When the user doesn't pass `--zdiff`, default to **90 Ω** with
**0.127 mm** gap — the USB-class bias is the safest assumption
because USB is the most common diff pair on hobbyist boards and
USB pairs that are accidentally too narrow degrade gracefully.

---

## Anti-patterns

- **Don't pick the solver's width without sanity-checking the
  fab minimum.** A 0.05 mm trace solves cleanly on the math but
  blows past every reasonable fab's 0.127 mm floor. Always run
  the M2-Q-03 DFM check after declaring.

- **Don't combine `/diffpair` and `/length-match` in one
  invocation.** They share state (the M3-R-04 router writes
  tracks that the M3-R-05 analyser reads), but coupling the
  approvals confuses the user. Always declare first; tune
  length in a separate `/length-match` call.
