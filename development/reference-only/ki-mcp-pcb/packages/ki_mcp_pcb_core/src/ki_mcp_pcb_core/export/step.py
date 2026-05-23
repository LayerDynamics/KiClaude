"""3D STEP export — ``kicad-cli pcb export step``."""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core._kicad_cli import run_kicad_cli


def export_step(pcb_path: Path, out_path: Path) -> Path:
    """Export a 3D STEP model of the board."""
    pcb_path = Path(pcb_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    run_kicad_cli(
        [
            "pcb",
            "export",
            "step",
            "--output",
            str(out_path),
            "--subst-models",
            "--force",
            str(pcb_path),
        ]
    )
    return out_path
