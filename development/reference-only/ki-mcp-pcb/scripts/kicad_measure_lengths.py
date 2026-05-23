#!/usr/bin/env python3
"""Measure routed trace lengths on a KiCad PCB.

Runs **inside KiCad's bundled Python**. For each net on the board, sums
the on-copper trace length (in mm) and emits a JSON report:

    {
      "pcb_path": "...",
      "nets": {
        "USB_DP":   {"length_mm": 35.18, "trace_count": 4},
        "USB_DM":   {"length_mm": 36.02, "trace_count": 5},
        "I2S_BCLK": {"length_mm": 18.7,  "trace_count": 3}
      }
    }

Used by ``synthesis/length_tuning.py`` to compare against declared
length-match group tolerances.

Usage:
    $KICAD_PYTHON scripts/kicad_measure_lengths.py \\
        --pcb path/to/board.kicad_pcb \\
        [--report report.json]

Exit codes:
    0 success
    2 pcbnew not importable
    4 PCB load failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _import_pcbnew():
    try:
        import pcbnew  # type: ignore[import-not-found]
    except ImportError as exc:
        sys.stderr.write(f"pcbnew not importable: {exc}\n")
        sys.exit(2)
    return pcbnew


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure routed trace lengths.")
    parser.add_argument("--pcb", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    pcbnew = _import_pcbnew()

    try:
        board = pcbnew.LoadBoard(str(args.pcb))
    except Exception as exc:
        sys.stderr.write(f"failed to load PCB: {exc}\n")
        return 4

    # Walk every track segment, group by net name.
    lengths_mm: dict[str, float] = {}
    counts: dict[str, int] = {}
    for track in board.GetTracks():
        # pcbnew represents tracks as either PCB_TRACK or PCB_ARC; both have
        # GetLength() (in native KiCad internal units — convert via ToMM).
        if not hasattr(track, "GetLength"):
            continue
        net = track.GetNetname() if hasattr(track, "GetNetname") else ""
        if not net:
            continue
        length_iu = track.GetLength()
        length_mm = pcbnew.ToMM(length_iu)
        lengths_mm[net] = lengths_mm.get(net, 0.0) + length_mm
        counts[net] = counts.get(net, 0) + 1

    payload = {
        "pcb_path": str(args.pcb),
        "nets": {
            name: {"length_mm": round(lengths_mm[name], 4),
                   "trace_count": counts[name]}
            for name in sorted(lengths_mm)
        },
    }
    text = json.dumps(payload, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
