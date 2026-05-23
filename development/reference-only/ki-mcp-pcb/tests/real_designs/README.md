# real_designs/

Hand-authored CIR for real reference designs. Doubles as a schema-completeness audit: anything we can't express in the current CIR is a gap noted in this README and the test file.

| Design | File | Notes / gaps |
|---|---|---|
| Adafruit QT Py — RP2040 | `rp2040_qtpy.yaml` | OK |
| Pi Pico (RP2040, USB-C, debug header) | `pi_pico.yaml` | OK; flash on QSPI not yet modeled (M2) |
| Trinket M0 (SAMD21, USB micro-B) | `trinket_m0.yaml` | USB diff pair impedance is M3 — left as a `# TODO` |

Gaps that surface during M0–M3 are tracked in `SPEC.md §12` (Open Questions) or filed as issues.
