---
name: pcb-review
description: Run the full M3 design-review skill end-to-end on the active PCB — stackup, net classes, diff pairs, length match, decoupling, DRC. Fans out to the decoupling-auditor and placement-explorer subagents in parallel for the heavy passes. Returns a single-line verdict (READY-TO-FAB / READY-TO-FAB WITH N WARNINGS / BLOCKED — N errors) plus a finding-by-finding breakdown.
allowed-tools:
  - mcp__kiclaude__kc_project_open
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_validate
  - mcp__kiclaude__kc_drc
  - mcp__kiclaude__kc_netclass_list
  - mcp__kiclaude__kc_impedance
  - mcp__kiclaude__kc_diffpair_declare
  - mcp__kiclaude__kc_length_match
---

# /pcb-review — fully gated design audit

The command is a thin orchestrator: it invokes the
[`design-review`](../skills/design-review/SKILL.md) skill, which
encodes the six-pass discipline. The command's value-add is:

- **Parallel dispatch** of the two passes that are agentic
  (decoupling-auditor + placement-explorer) so the heavy work
  runs concurrently instead of serially.
- **Single-line verdict** at the end suitable for paste-back into
  any chat or PR comment.
- **Per-finding severity bucketing** so the user can fix the
  blockers first and ignore the advisories.

---

## Flow

1. `kc_validate` — confirm the project file is saved + parseable.
   Abort with a clear "save the project first" message otherwise.

2. **Sync passes** (run inline, sub-second each):
   - Stackup correctness
   - Net-class clearance vs fab rules
   - Diff-pair routing health (impedance + skew)
   - Length-match group status

3. **Parallel subagent dispatch** (M3-P-07 registry):
   - `decoupling-auditor` — walks every IC footprint, flags
     missing bypass caps.
   - `placement-explorer` — flags footprints whose courtyards
     overlap with declared keep-outs or violate the M3-Q-05
     SLO-relevant connectivity heuristics.
   Both subagents emit findings on the same severity scale as
   the inline passes.

4. **DRC** — final pass via `kc_drc` (kicad-cli, fab source of
   truth per SPEC §16.1 D8).

5. **Aggregate + verdict** — collect every finding (inline +
   subagent + DRC), bucket by severity, emit the one-line
   verdict + the finding list.

---

## Verdict shape

```text
BLOCKED — 3 errors, 2 warnings
  [error] stackup: layer 2 (In1.Cu) missing dielectric below
  [error] net-class POWER clearance 0.1 mm vs OSHPark 0.1524 mm
  [error] length-match DDR_BYTE0/DQ2 TooLong by 0.8 mm
  [warning] decoupling: U3 pin 14 (VDD) lacks bypass cap within 2 mm
  [warning] placement: U7 courtyard overlaps board-edge keepout (0.3 mm)
```

When everything's green:

```text
READY-TO-FAB
  6 passes clean, 0 findings
```

---

## What this command is NOT

- **Not a fix command.** Findings surface only — the user has to
  drive `/drc-fix`, `/length-match`, or `/add-decoupling`
  themselves to close them.
- **Not the export-time DFM check.** M2-Q-03 / `pcb-fab` runs
  the DFM gate at export. This command is the *pre-export*
  audit; running both is normal.
- **Not a replacement for `kicad-cli pcb drc`.** It calls it,
  but adds the M3-aware passes that kicad-cli doesn't know
  about (impedance, length match, diff-pair declaration health).
