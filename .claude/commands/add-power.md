---
name: add-power
description: Add a complete power subsystem onto the active sheet. Usage `/add-power <topology>` where topology is `buck-3v3-from-vbus`, `ldo-3v3-from-vbus`, `boost-5v-from-3v3`, or `ldo-1v8-from-3v3`. ERC-clean afterward.
argument-hint: "<topology: buck-3v3-from-vbus | ldo-3v3-from-vbus | boost-5v-from-3v3 | ldo-1v8-from-3v3>"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_validate
  - mcp__kiclaude__kc_erc
  - mcp__kiclaude__kc_mpn_resolve
  - mcp__kiclaude__kc_symbol_add
  - mcp__kiclaude__kc_symbol_edit
  - mcp__kiclaude__kc_wire_connect
  - mcp__kiclaude__kc_label_attach
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_snapshot_revert
  - mcp__kiclaude__kc_project_save
---

# /add-power — drop a power-conversion subsystem

Argument: `$ARGUMENTS` — the topology. Supported values:

| Topology | Default IC | Inductor / cap notes |
|---|---|---|
| `buck-3v3-from-vbus` | TPS562201 (1.5 A buck) | 4.7 µH inductor, 22 µF in + 22 µF out |
| `ldo-3v3-from-vbus` | AP2112K-3.3 (600 mA LDO) | 1 µF in + 1 µF out |
| `boost-5v-from-3v3` | TPS61222 (200 mA boost) | 4.7 µH inductor, 4.7 µF in + 22 µF out |
| `ldo-1v8-from-3v3` | TLV70218 (300 mA LDO) | 1 µF in + 1 µF out |

Anything else → stop and list the supported topologies.

## Sequence

1. **Snapshot.**
   `kc_snapshot_create(project_id, label="before /add-power <topology>")`.

2. **Resolve every MPN.** Step through the parts list (regulator,
   inductor [if any], input cap, output cap, optional feedback
   resistors) and call `kc_mpn_resolve` on each. Stop and ask if
   any returns `found: false`.

3. **Place the parts** with declarative hints:
   - Regulator goes "near the input rail label" — usually `+5V` or
     `VBUS` for the input side.
   - Caps go "near regulator IN" / "near regulator OUT".
   - Inductor (buck/boost only) goes "between SW pin and output cap".
   - Feedback resistors (if the IC has an adjustable output) go
     "between FB pin and output rail".

4. **Wire the subsystem.**
   - Input pin → input-rail global label (e.g. `VBUS`).
   - Output pin → output-rail global label (e.g. `+3V3`).
   - EN pin → input-rail label (always-on) **unless** the user asks
     for a soft-start hook.
   - Feedback divider (if any) → output rail through R<top>, then
     FB node to GND through R<bottom>. Pick resistors so that
     `Vout = Vref × (1 + Rtop / Rbottom)`; cite the IC's `Vref`
     from the datasheet in the chat reply.
   - GND pin → `GND` global label.

5. **Add the output label** as `global` so other sheets pick up the
   new rail without further wiring.

6. **Validate.**
   - `kc_validate` → must not introduce any new KC001..KC011 errors.
   - `kc_erc(project_id, project_path)` → must be clean. If ERC
     finds an issue, fix it in a follow-up tool call before the
     command declares done.

7. **Save and summarize.**
   - `kc_project_save` once ERC is clean.
   - Summary block:

     ```
     added power subsystem ($ARGUMENTS):
       - U? (<regulator MPN>)
       - L? (<inductor MPN>)            [if applicable]
       - 2× input caps, 2× output caps
       - feedback divider R?, R?         [if applicable]
     new rail: <output rail label>
     ERC: clean (0 errors, N warnings)
     ```

## Notes for Claude

- **Never** invent MPNs — every part must resolve cleanly. The
  default ICs above are starting points; if the user has a
  preference (e.g. "use the LMR16006Y we already have on another
  board"), call `kc_mpn_resolve` on that part and use it instead.
- **Inductor saturation matters.** For buck/boost topologies, surface
  the saturation-current spec for the chosen inductor in the chat
  reply so the user can verify it against their expected load.
- **The output rail name is load-bearing.** If a sheet already has a
  `+3V3` global label, do not add a second `+3V3` source unless the
  user explicitly approves a dual-supply design — flag this case and
  ask first. Two unrelated supplies sharing one global label is a
  systemic short-circuit risk.
- **Always run ERC after this command**, even if the user says "no
  ERC needed". The acceptance criterion is "ERC clean".
