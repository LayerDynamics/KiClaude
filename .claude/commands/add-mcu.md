---
name: add-mcu
description: Add a microcontroller subsystem (STM32 / ESP32 / RP2040) to the active schematic — MCU + USB + LDO + decoupling caps, ERC-clean. Usage `/add-mcu <family>` where family is one of `esp32-s3`, `stm32f411`, `rp2040`.
argument-hint: "<mcu family: esp32-s3 | stm32f411 | rp2040>"
allowed-tools:
  - mcp__kiclaude__kc_project_open
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

# /add-mcu — drop an MCU subsystem onto the active sheet

Argument: `$ARGUMENTS` — the MCU family. Supported values:

| Family | Package | USB | Crystal | Power rail |
|---|---|---|---|---|
| `esp32-s3` | QFN-56 (WROOM-1 module) | USB 2.0 OTG (native) | none (internal) | 3V3 (LDO from VBUS) |
| `stm32f411` | LQFP-48 | USB 2.0 device | 25 MHz HSE | 3V3 (LDO from VBUS) |
| `rp2040` | QFN-56 | USB 1.1 device (native) | 12 MHz XOSC | 3V3 (LDO from VBUS) |

Anything else → stop, explain, and ask the user to pick a supported
family. Do **not** silently substitute an unrelated part.

## Sequence

1. **Take a manual snapshot first.** This command makes many tool
   calls; one revert button should roll back all of them.
   `kc_snapshot_create(project_id, label="before /add-mcu <family>")`.

2. **Resolve the MPN before placing.** For each part below, call
   `kc_mpn_resolve` and only proceed if `found: true`. If a part
   can't be resolved, stop and ask the user — never guess.

3. **Add the MCU + supporting parts on the active sheet.** Use
   declarative hints; never pass coordinates. Suggested call order:

   - MCU (`U?`)
   - USB connector — `USB_C_Receptacle_USB2.0_16P` for esp32-s3 and
     stm32f411; `USB_B_Micro` for rp2040 (simpler, matches Pi Pico
     reference design).
   - LDO — `AP2112K-3.3` (esp32-s3, rp2040) or `NCP1117ST33T3G`
     (stm32f411).
   - Decoupling caps: one 10 µF (bulk) + one 100 nF (HF) per power
     pin on the MCU. Place with hints like `["near U? VDD pin"]`.
   - Crystal (`X?`) + two 18 pF load caps for stm32f411 and rp2040.
     Skip for esp32-s3 (internal oscillator).
   - Boot strap pull-ups: 10 kΩ on BOOT0 / EN as the family requires.

4. **Wire the subsystem.**
   - Power: connect USB VBUS → LDO IN, LDO OUT → all MCU VDD pins
     via `+3V3` global label. Tie all VSS/GND to `GND` global label.
   - USB data: D+/D− straight to the MCU's native USB pins (esp32-s3
     GPIO19/20; rp2040 GPIO16/17; stm32f411 PA11/PA12). For
     stm32f411, also add the 1.5 kΩ pullup on D+ tied to a GPIO if
     the family requires soft-attach.
   - Crystal: route XTAL pins through the load caps to GND.

5. **Validate.**
   - `kc_validate` → report KC001..KC011 findings. Any error stops
     the command — propose a fix and ask the user.
   - `kc_erc(project_id, project_path)` → must come back with zero
     errors. Warnings are surfaced but don't block; explain each.

6. **Save.** Only after ERC reports clean: `kc_project_save`. Quote
   the list of written paths to the user.

7. **Summary message.** End with a single-block diff-style summary:

   ```
   added MCU subsystem ($ARGUMENTS):
     - U? (<MCU MPN>)
     - U? (LDO)
     - J? (USB-C)
     - X? + 2x C (crystal load) [if applicable]
     - N decoupling caps
     - 1 boot strap pull-up
   wired: +3V3, GND, USB D+/D−, XTAL net
   ERC: clean (0 errors, N warnings)
   ```

## Notes for Claude

- **Never** invent MPNs. The /add-mcu workflow only succeeds if every
  part can be resolved against Digikey / Mouser / LCSC.
- **Never** issue raw-coordinate placements. The MCU goes "near the
  USB connector"; the decoupling caps go "near the MCU VDD pins".
  kiclaude's placement engine resolves these against live geometry.
- **If the active sheet already has a microcontroller**, ask before
  adding another. Two MCUs on one sheet is usually a mistake.
- **The acceptance criterion is ERC-clean**, not "looks right". If
  ERC fails, fix the errors before declaring done.
