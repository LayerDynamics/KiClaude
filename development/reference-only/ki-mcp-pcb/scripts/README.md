# scripts/

Standalone reusable scripts (per SPEC.md §10). Each is also wrapped as a `kimp` subcommand and an MCP tool — these are the lowest-level entry points for one-shot use.

| Script | Purpose |
|---|---|
| `validate_cir.py` | Run CIR-level structural validation against a YAML file. |
| `nl_to_ato.py` | (M1) Natural-language description → `.ato` draft. |
| `bom_price.py` | (M1) CSV BOM → priced + in-stock CSV. |
| `footprint_pick.py` | (M1) MPN → suggested KiCad footprint. |
| `drc_summary.py` | (M1) `kicad-cli` DRC JSON → human-readable triage. |
| `gerber_pack.py` | (M1) Routed board → fab-target-specific zip. |
| `decoupling_check.py` | (M2) ICs missing nearby bypass caps. |
| `length_audit.py` | (M3) Declared vs. actual lengths for matched groups. |
| `panelize.py` | (M3) Single board → v-score / mouse-bite panel. |
| `step_render.py` | (M2) Board → 3D STEP + rendered PNG. |
