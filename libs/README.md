# `libs/` — bundled KiCad library mirror (pinned)

This directory is kiclaude's **bundled symbol/footprint mirror** (SPEC §9.5,
§12, decision D6, FR-040). It ships a *pinned, curated subset* of the official
KiCad libraries — exactly the libraries the projects under `examples/`
reference — so a project resolves its parts **fully offline** (first principle
#8, local-first). It is deliberately **not** the full multi-GB mirror.

## Layout

```
libs/
├── MANIFEST.toml     # pinned record: per-file {kind, nickname, url, sha256}
├── LICENSE.md        # CC-BY-SA 4.0 + KiCad Library Exception attribution
├── sym-lib-table     # registers the bundled symbol libs (${KICLAUDE_BUNDLED_LIBS})
├── fp-lib-table      # registers the bundled footprint libs
├── symbols/          # whole .kicad_sym libraries (Device, power, Connector, …)
└── footprints/       # the exact .kicad_mod files the examples place
```

## How it is indexed

`kiserver`'s `GET /project/{id}/library/search` route resolves the bundled
mirror via `bundled_libs_dir()`, which honours `$KICLAUDE_BUNDLED_LIBS` (set so
the `${KICLAUDE_BUNDLED_LIBS}` URIs in the lib-tables resolve regardless of
where the repo lives) and otherwise falls back to this in-repo directory. Hits
from the project's own `sym-lib-table` rank first; the bundled mirror fills in
the standard KiCad parts (FR-040: "the user's local libraries **and** the
bundled mirror").

## Refreshing / re-pinning (D6)

```bash
# Verify every committed file against MANIFEST.toml (fetches anything missing):
python scripts/populate_libs.py

# Re-fetch all curated libraries from GitLab at the pinned tag and regenerate
# MANIFEST.toml with fresh SHA-256 pins (run after editing the curated set or
# bumping the tag in scripts/populate_libs.py):
python scripts/populate_libs.py --pin
```

To grow the curated subset (e.g. when a new example needs another library),
add an `Entry` to `_CURATED` in `scripts/populate_libs.py` and re-run `--pin`.
