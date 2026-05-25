---
name: add-usb-c
description: Add a USB-C connector to the active schematic — always with the mandatory CC pull-down resistors and VBUS bulk cap; optionally a PD trigger IC (--pd) and/or USB 2.0 data pair wiring (--data). ERC-clean afterward. Each placement is gated through the M1-P-06 PreToolUse approval.
argument-hint: "[--pd <volts>] [--data]   --pd adds a PD-trigger requesting that voltage (e.g. --pd 9); --data wires D+/D- as a 90 Ω diff pair; default: power-only sink"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_symbol_add
  - mcp__kiclaude__kc_symbol_edit
  - mcp__kiclaude__kc_wire_connect
  - mcp__kiclaude__kc_label_attach
  - mcp__kiclaude__kc_mpn_resolve
  - mcp__kiclaude__kc_diffpair_declare
  - mcp__kiclaude__kc_erc
  - mcp__kiclaude__kc_snapshot_create
  - mcp__kiclaude__kc_project_save
---

# /add-usb-c — USB-C connector (power, optional PD + data)

Adds a USB-C receptacle wired as a **sink** by default, with the
non-negotiable bits a USB-C port needs to be legal and not back-feed.
PD and data are opt-in.

## Flow

1. **Read context** — `kc_kcir_get` for the sheet + the VBUS/GND rails.
2. **Snapshot** — `kc_snapshot_create`.
3. **Connector** — `kc_symbol_add` a USB-C receptacle
   (`Connector:USB_C_Receptacle_USB2.0` for the 2.0 part; use the
   power-only 16-pin variant if `--data` is absent). Resolve an MPN via
   `kc_mpn_resolve` so the BOM is real.
4. **Mandatory sink wiring (always):**
   - **CC1 / CC2 each get a 5.1 kΩ pull-down to GND** (`Rd`). This is
     what declares the port a sink and lets the source apply VBUS —
     skipping it is the #1 USB-C bug. Two resistors, not one.
   - **VBUS bulk cap** (e.g. 10 µF) + GND.
   - **Shield → GND** (often through a small RC/ferrite; a direct tie is
     acceptable for a simple sink — note the choice).
5. **`--data` (optional):** wire D+/D- to the target (MCU/hub). Declare
   the pair with `kc_diffpair_declare` (USB 2.0 → 90 Ω Zdiff, 0.127 mm
   gap) so the PCB side routes it controlled-impedance. On a 2.0 part,
   tie the two CC-side SBU/extra D pairs per the receptacle's datasheet.
6. **`--pd <volts>` (optional):** add a PD-trigger IC (e.g. an
   HUSB238-class sink controller) configured to request the given
   voltage; wire its CC lines **instead of** the bare 5.1 kΩ Rd (the
   trigger owns CC negotiation). Add its required decoupling. Surface
   that the downstream rail must tolerate the requested voltage.
7. **ERC** — `kc_erc`; clear new warnings (floating CC, missing GND).
8. **Save** — `kc_project_save`.

## Anti-patterns

- **Never ship USB-C without the CC pull-downs (or a PD/CC controller).**
  Without `Rd`, a compliant source delivers no VBUS — the board looks
  dead. This is the single most common USB-C mistake.
- **Don't request a PD voltage the board can't survive.** `--pd 20`
  feeding a 3.3 V LDO with a low abs-max is a fire. Check the
  downstream rail's input rating before wiring PD.
- **One bulk cap is not decoupling.** `/add-usb-c` adds the VBUS bulk
  cap; per-IC bypass is still `/add-decoupling`'s job.
