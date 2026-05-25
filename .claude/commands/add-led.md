---
name: add-led
description: Add a status LED + its current-limit resistor to the active schematic, wired between a chosen driver pin (or a power rail) and ground, sized for the rail voltage. ERC-clean afterward. Each placement is gated through the M1-P-06 PreToolUse approval.
argument-hint: "[pin]   the net/refdes-pin to drive the LED (e.g. U1-IO2); defaults to a free GPIO or, if none, the +3V3 rail as a power indicator"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_symbol_add
  - mcp__kiclaude__kc_symbol_edit
  - mcp__kiclaude__kc_wire_connect
  - mcp__kiclaude__kc_label_attach
  - mcp__kiclaude__kc_mpn_resolve
  - mcp__kiclaude__kc_erc
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_project_save
---

# /add-led — status LED + current-limit resistor

Adds the smallest useful indicator subsystem: one LED, one series
resistor, wired `<driver> → R → LED → GND` (or `rail → R → LED → GND`
for a plain power indicator).

## Flow

1. **Read context** — `kc_kcir_get` for the active sheet, the available
   rails, and (if `[pin]` was given) the driver net. Determine the
   drive voltage (GPIO high ≈ the MCU rail; rail-indicator ≈ the rail).
2. **Pick the driver** — use `$ARGUMENTS` if supplied. Otherwise prefer
   a free GPIO; if none is obvious, fall back to a `+3V3`/`+5V` power
   indicator and say so (don't silently commandeer a signal pin).
3. **Size the resistor** — `R = (Vdrive − Vf) / Iled`. Defaults:
   `Vf ≈ 2.0 V` (red/yellow) or `≈ 3.0 V` (green/blue/white), `Iled ≈
   2 mA` for a modern indicator (bright + low-power). Round to the
   nearest E24 value (e.g. 3V3 GPIO, red, 2 mA → `(3.3−2.0)/0.002 =
   650 Ω` → `680 Ω`). State the math.
4. **Snapshot** — `kc_snapshot_create` before adding parts.
5. **Place the parts** — `kc_symbol_add` for `Device:LED` and
   `Device:R`; `kc_symbol_edit` to set the resistor value, the LED
   colour/value, and (best-effort) an MPN via `kc_mpn_resolve` so the
   BOM resolves. Refdes auto-assigned.
6. **Wire** — `kc_wire_connect` the driver→R, R→LED anode, LED
   cathode→GND (use `kc_label_attach` for the GND power label rather
   than a long wire). Each mutating call goes through the approval gate.
7. **ERC** — `kc_erc`; resolve any new unconnected-pin / no-driver
   warnings before saving.
8. **Save** — `kc_project_save`.

## Defaults / anti-patterns

- **2 mA, not 20 mA.** Modern indicator LEDs are plenty bright at
  1–2 mA; 20 mA wastes power and can exceed a GPIO's source limit.
- **Always add the resistor.** A bare LED on a rail is a request for a
  dead LED. If the user insists on no resistor (constant-current
  driver), confirm explicitly.
- Don't drive an LED directly from a pin that can't source the current
  — check the MCU's per-pin limit; use a transistor/FET for high-side
  loads (out of scope for `/add-led`; suggest `/add-power` patterns).
