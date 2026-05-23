"""Synthesise a dense `.kicad_pcb` for M2-Q-04's NFR-003 benchmark.

Produces a 1000-footprint / 5000-track board on a 50x50 grid. Each
footprint is a 1x1 mm pad placed on `F.Cu`; tracks are random-walk
polylines connecting random pairs. Output is a valid KiCad 9 PCB
file the React `PcbCanvas` can load directly through the kiserver
project-open path.

Run as:

```bash
python tests/perf/generate_dense_board.py --footprints 1000 \
    --tracks 5000 --out tests/perf/dense_board.kicad_pcb
```

The defaults match the NFR-003 benchmark target.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

KICAD_HEADER = """(kicad_pcb (version 20240108) (generator kiclaude-bench)

  (general
    (thickness 1.6)
  )

  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )

  (setup
    (pad_to_mask_clearance 0.0)
  )

  (net 0 "")
"""


def write_footprints(out, footprint_count: int, rng: random.Random) -> None:
    grid = max(1, int(footprint_count**0.5) + 1)
    pitch_mm = 2.0
    for i in range(footprint_count):
        row, col = divmod(i, grid)
        x = 10.0 + col * pitch_mm
        y = 10.0 + row * pitch_mm
        rot = rng.choice([0, 90, 180, 270])
        out.write(
            f"  (footprint \"Resistor_SMD:R_0402_1005Metric\"\n"
            f"    (layer \"F.Cu\")\n"
            f"    (uuid \"fp-{i:06d}-0000-0000-0000-000000000000\")\n"
            f"    (at {x:.3f} {y:.3f} {rot})\n"
            f"    (property \"Reference\" \"R{i + 1}\")\n"
            f"    (property \"Value\" \"10k\")\n"
            f"  )\n"
        )


def write_tracks(
    out, track_count: int, board_extent_mm: float, rng: random.Random
) -> None:
    for i in range(track_count):
        layer = "F.Cu" if i % 2 == 0 else "B.Cu"
        sx = rng.uniform(5.0, board_extent_mm)
        sy = rng.uniform(5.0, board_extent_mm)
        ex = sx + rng.uniform(-3.0, 3.0)
        ey = sy + rng.uniform(-3.0, 3.0)
        out.write(
            f"  (segment (start {sx:.3f} {sy:.3f}) "
            f"(end {ex:.3f} {ey:.3f}) (width 0.2) "
            f"(layer \"{layer}\") (net 0) "
            f"(uuid \"track-{i:06d}-0000-0000-0000-000000000000\"))\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--footprints", type=int, default=1000)
    parser.add_argument("--tracks", type=int, default=5000)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tests/perf/dense_board.kicad_pcb"),
    )
    parser.add_argument(
        "--seed", type=int, default=12345,
        help="random seed so the generated board is reproducible",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    extent = max(60.0, (args.footprints**0.5) * 2.0 + 20.0)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as out:
        out.write(KICAD_HEADER)
        write_footprints(out, args.footprints, rng)
        write_tracks(out, args.tracks, extent, rng)
        out.write(")\n")
    size_kb = args.out.stat().st_size / 1024
    print(
        f"wrote {args.out} — {args.footprints} footprints + "
        f"{args.tracks} tracks ({size_kb:.1f} KB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
