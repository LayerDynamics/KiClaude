"""Gerber + drill export — wraps ``kicad-cli pcb export gerbers`` and ``drill``."""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core._kicad_cli import run_kicad_cli

# JLCPCB-friendly Gerber settings. We keep these defaults conservative; per-fab
# overrides come through ``FabTarget`` at the call site.
DEFAULT_GERBER_LAYERS = (
    "F.Cu",
    "B.Cu",
    "F.Paste",
    "B.Paste",
    "F.Silkscreen",
    "B.Silkscreen",
    "F.Mask",
    "B.Mask",
    "Edge.Cuts",
)


def export_gerbers(
    pcb_path: Path,
    out_dir: Path,
    *,
    layers: tuple[str, ...] = DEFAULT_GERBER_LAYERS,
    use_drill_origin: bool = True,
) -> list[Path]:
    """Generate Gerbers from a routed board. Returns the list of files created."""
    pcb_path = Path(pcb_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    args = [
        "pcb",
        "export",
        "gerbers",
        "--output",
        str(out_dir),
        "--layers",
        ",".join(layers),
    ]
    if use_drill_origin:
        args.append("--use-drill-file-origin")
    args.append(str(pcb_path))

    run_kicad_cli(args)

    # kicad-cli names files <board>-<layer>.gbr; enumerate what landed.
    return sorted(out_dir.glob("*.gbr")) + sorted(out_dir.glob("*.gm1"))


def export_drill(
    pcb_path: Path,
    out_dir: Path,
    *,
    excellon_format: bool = True,
    use_drill_origin: bool = True,
) -> list[Path]:
    """Generate drill files. Excellon by default (JLC-friendly)."""
    pcb_path = Path(pcb_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    args = [
        "pcb",
        "export",
        "drill",
        "--output",
        str(out_dir) + "/",
        "--format",
        "excellon" if excellon_format else "gerber",
    ]
    if use_drill_origin:
        args.append("--drill-origin")
        args.append("plot")
    args.append(str(pcb_path))

    run_kicad_cli(args)

    return sorted(out_dir.glob("*.drl"))
