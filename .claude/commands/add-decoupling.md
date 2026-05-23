---
name: add-decoupling
description: Scan all ICs on the active project for missing bypass capacitors and propose one cap (or pair) per missing pin. The user approves each proposal via the M1-P-06 PreToolUse gate before any cap lands.
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_validate
  - mcp__kiclaude__kc_erc
  - mcp__kiclaude__kc_mpn_resolve
  - mcp__kiclaude__kc_symbol_add
  - mcp__kiclaude__kc_wire_connect
  - mcp__kiclaude__kc_label_attach
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
---

# /add-decoupling — bypass-cap audit + remediation

Walks the live KCIR for every integrated circuit (refdes prefix `U`)
and surfaces power pins that lack a nearby decoupling capacitor.
Proposes one fix per finding; the M1-P-06 PreToolUse gate prompts
the user before each cap is actually added (see SPEC §A.3).

## Sequence

1. **Snapshot first.**
   `kc_snapshot_create(project_id, label="before /add-decoupling")`.
   The whole batch is revertable from the ActivityJournal with one
   click.

2. **Load the live KCIR.**
   `kc_kcir_get(project_id, view=["schematic"])`.

3. **Build the audit list.** For each symbol whose `refdes` starts
   with `U`:
   - Identify power pins (`pin.type == "power_in"` or pin name
     matches `VDD*`, `VCC*`, `AVDD`, `VBAT`, `IOVCC`, …).
   - For each power pin, walk the wires from that pin and check
     whether any nearby symbol is a capacitor connected to GND.
     "Nearby" = on the same sheet AND wired into the same net
     within ≤ 2 wire-hops.
   - A pin is **covered** if it has at least one HF cap (≤ 1 µF)
     plus, for digital ICs with > 50 mA load, a bulk cap (≥ 10 µF).
     Surface uncovered pins as findings.

4. **Pick parts.** For HF caps: `kc_mpn_resolve("100nF 0603 X7R 25V")`.
   For bulk caps: `kc_mpn_resolve("10uF 0805 X5R 10V")`. Stop if
   either lookup returns `found: false` — ask the user for a
   substitute.

5. **Propose one cap per finding, one tool call at a time.**
   `kc_symbol_add` + `kc_wire_connect` + `kc_label_attach` for each.
   Use declarative hints — `["near U1 VDD pin"]`. The permission
   gate will pause each mutation until the user clicks Approve;
   if they Deny, move to the next finding (do **not** retry
   automatically).

6. **Validate at the end of the batch.**
   `kc_validate(project_id)` + `kc_erc(project_id, project_path)`.
   Report findings exactly — do not soften error messages.

7. **Summary block.**

   ```
   audited <N> ICs, <M> power pins
   added <K> decoupling caps (J approvals, K-J denials)
   ERC: clean (0 errors, N warnings)
   ```

## Notes for Claude

- The audit is read-only until step 5. Steps 2–4 must not mutate the
  project — they're surveying.
- **Never** add a cap the user denied. If they deny three in a row,
  stop the command and ask whether the rule should be relaxed for
  this design.
- If the IC already has *exactly* the right cap arrangement, say so
  ("U2 — covered, 100 nF + 10 µF on VDD") rather than skipping
  silently. The user wants the audit transcript even when the answer
  is "no change needed".
- High-current rails (5V to a motor driver, etc.) need more than the
  default HF+bulk pair. If you detect a switching regulator output
  or motor-driver IC, propose the manufacturer's recommended cap
  bank instead of the default; cite the datasheet section in the
  message.
