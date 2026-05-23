"""Pick-and-place CSV export — ``kicad-cli pcb export pos``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ki_mcp_pcb_core._kicad_cli import run_kicad_cli

Side = Literal["both", "front", "back"]


def export_pick_and_place(
    pcb_path: Path,
    out_path: Path,
    *,
    side: Side = "both",
    use_drill_origin: bool = True,
) -> Path:
    """Export the P&P CSV for SMT assembly. JLC-friendly defaults."""
    pcb_path = Path(pcb_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "pcb",
        "export",
        "pos",
        "--output",
        str(out_path),
        "--format",
        "csv",
        "--units",
        "mm",
        "--side",
        side,
    ]
    if use_drill_origin:
        args.append("--use-drill-file-origin")
    args.append(str(pcb_path))

    run_kicad_cli(args)
    return out_path
