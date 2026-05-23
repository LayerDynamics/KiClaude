---
name: length-match
description: Analyse declared length-match groups on the active PCB, surface deltas vs target, and queue serpentine tuning proposals. Each tuning batch is gated through the M1-P-06 PreToolUse approval before any track is rewritten.
argument-hint: "[--group <name>]   default: all groups declared on the PCB"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_length_match
  - mcp__kiclaude__kc_tune_serpentine
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /length-match — measure and close length-skew groups

Length matching is the third leg of high-speed routing (after stackup
+ controlled-impedance). The analyzer reads `pcb.length_groups`,
computes the routed length of every member net, and proposes
serpentine segments to close any shortfall within tolerance.

`TooLong` members surface as **errors** with no auto-fix — the user
has to re-route the long member shorter; the command refuses to
proceed with tuning until those are resolved.

---

## Flow

1. **Snapshot** — `kc_snapshot_create` so the tuning batch is one-
   click revertable from the M1-T-08 activity journal.
2. **Analyse** — `kc_length_match` returns one report per declared
   group with per-net status, current length, delta vs target, and
   (for `TooShort` members) suggested serpentine count + per-segment
   length gain. Summarise back to the user as a table.
3. **Refuse on `TooLong`** — if ANY group has at least one
   `TooLong` member, stop with a clear message. The user must
   re-route that net shorter before tuning can proceed.
4. **Per-group approval** — for each group with at least one
   `TooShort` member, propose the full tuning queue (which nets,
   how many serpentines each, where each serpentine should land)
   as one PreToolUse batch. User approves; the command calls
   `kc_tune_serpentine` per member in order.
5. **Re-measure** — after each batch, call `kc_length_match`
   again. Stop when every group reads `InRange` or the user
   bails.
6. **DRC** — run `kc_drc` once at the end. Serpentines often
   cross clearance lines; surface any new violations as a
   pre-save block.
7. **Save** — `kc_project_save` only after DRC is clean.

---

## Behaviour rules

- **Never tune a single member of a group in isolation.** Match-the-
  longest groups (declared with `target_length_mm == 0`) re-compute
  their implicit target every analyser run; tuning one member in
  isolation can shift the target and leave the other members worse off.
  Always batch all `TooShort` members of a group into one approval.

- **Surface the unrouted members.** If a group contains an
  `Unrouted` member, mention it explicitly — tuning the rest of
  the group is fine, but the user needs to know they have a
  missing route.

- **Per-segment gain ≤ MAX_SEGMENT_GAIN_MM**. The analyzer
  already enforces this; don't override the suggestion.

---

## Example

```text
> /length-match --group RGMII_TX
RGMII_TX target 50.000 mm tolerance ±0.500 mm
  TX0:  49.8 mm  Δ -0.2 mm  InRange
  TX1:  45.0 mm  Δ -5.0 mm  TooShort — propose 1 serpentine of 5.0 mm
  TX2:  56.0 mm  Δ +6.0 mm  TooLong  — must re-route
Refusing to tune: 1 TooLong member in RGMII_TX.
Re-route TX2 shorter and try again.
```
