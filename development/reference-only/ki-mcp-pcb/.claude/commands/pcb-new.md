---
description: Start a new PCB from a natural-language description.
argument-hint: <board description>
---

You are starting a new PCB project. The user described it as:

$ARGUMENTS

Follow this flow strictly. Do not skip steps.

1. **Clarify scope** before any tool calls. Ask the user (one batched question if multiple are needed):
   - Target fab? (default: JLCPCB)
   - Layer count and board size envelope?
   - Any hard part choices (specific MCU/connector/IC) vs. let-me-suggest?
   - Budget per unit and target quantity?

2. **Draft a `.ato` spec** based on the answers. Save it under `boards/<short-name>/<short-name>.ato`. Show the user the draft and wait for explicit "go" before proceeding.

3. **Validate** the draft via the MCP tool `pcb_validate_cir` (use the YAML equivalent for now — `.ato` parsing lands in M1). Fix any structural issues before continuing.

4. **Synthesize** via `pcb_synthesize` (M1+). Until then, stop here and tell the user the pipeline is at the M0 milestone.

5. Never invent MPNs. Every component needs a real, in-stock part number — confirm with the user when picking jellybeans.

Refer to `SPEC.md` and `CLAUDE.md` at the repo root for full rules.
