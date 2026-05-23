#!/usr/bin/env python3
"""Generate KiCad-filled reference zones for the M2-R-05b golden test.

For each `<name>_input.kicad_pcb` under `tests/golden/zones/`, loads the
board, fills its zones with pcbnew's `ZONE_FILLER`, and saves the
result as `<name>_filled.kicad_pcb`. The Rust comparison test reads
both, runs `kiclaude_cad::fill_zone` on the input, and asserts the
output polygons match the KiCad-emitted `filled_polygon` blocks within
0.01 mm Hausdorff distance.

Run this with KiCad's bundled Python interpreter so `pcbnew` resolves
to the version that ships with the installed KiCad app. Default path on
macOS is:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
        tests/golden/zones/generate_fixtures.py

Exits non-zero on any failure so this script doubles as a CI gate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import pcbnew  # type: ignore[import-not-found]
except ImportError as exc:
    sys.stderr.write(
        "pcbnew module not importable — run this script with KiCad's "
        "bundled Python (see module docstring). Underlying error: "
        f"{exc}\n",
    )
    sys.exit(2)


HERE = Path(__file__).resolve().parent


def fill_one(input_path: Path, output_path: Path) -> None:
    """Load `input_path`, fill every zone, save to `output_path`."""
    print(f"[zones] filling {input_path.name} → {output_path.name}", flush=True)
    board = pcbnew.LoadBoard(str(input_path))
    zones = list(board.Zones())
    if not zones:
        raise SystemExit(f"{input_path.name} has no zones to fill")
    filler = pcbnew.ZONE_FILLER(board)
    # `Fill` signature changed between KiCad 7/8/9/10; try the
    # 1-argument form first, fall back to the no-argument form.
    try:
        filler.Fill(zones)
    except TypeError:
        filler.Fill()
    # Confirm at least one filled polygon landed.
    total_pts = sum(
        zone.GetFilledPolysList(zone.GetLayerSet().Seq()[0]).OutlineCount()
        for zone in zones
    )
    if total_pts == 0:
        raise SystemExit(f"{input_path.name}: ZONE_FILLER produced no polygons")
    pcbnew.SaveBoard(str(output_path), board)


def main() -> int:
    if not HERE.is_dir():
        print(f"fixture dir missing: {HERE}", file=sys.stderr)
        return 1
    inputs = sorted(HERE.glob("*_input.kicad_pcb"))
    if not inputs:
        print(f"no *_input.kicad_pcb files under {HERE}", file=sys.stderr)
        return 1
    for input_path in inputs:
        stem = input_path.stem.removesuffix("_input")
        output_path = HERE / f"{stem}_filled.kicad_pcb"
        fill_one(input_path, output_path)
    print("[zones] generated", len(inputs), "filled fixtures", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
