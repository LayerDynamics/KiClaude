"""Fab package — bundles gerbers + drill + P&P + BOM into a fab-target zip.

This is the orchestrator. The individual exporters live in sibling
modules; this file picks the right combinations per fab house and
packages them.
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.export.bom import write_bom_csv
from ki_mcp_pcb_core.export.gerbers import export_drill, export_gerbers
from ki_mcp_pcb_core.export.pick_and_place import export_pick_and_place


@dataclass(frozen=True)
class FabPackage:
    directory: Path
    zip_path: Path
    fab_target: str
    files: list[Path]


def export_fab_package(
    board: Board,
    pcb_path: Path,
    out_dir: Path,
    *,
    fab_target: str | None = None,
) -> FabPackage:
    """Produce a fab-house-flavored zip from a routed board + CIR.

    Steps:
      1. Run gerber + drill + P&P exporters into ``out_dir/_raw/``.
      2. Write a BOM CSV directly from the CIR.
      3. Zip the package using the fab target's expected filename layout.
    """
    fab_target = fab_target or board.fab.name
    out_dir = Path(out_dir)
    raw = out_dir / "_raw"
    raw.mkdir(parents=True, exist_ok=True)

    gerber_files = export_gerbers(pcb_path, raw)
    drill_files = export_drill(pcb_path, raw)
    pos_file = export_pick_and_place(pcb_path, raw / f"{board.name}-pos.csv")
    bom_file = write_bom_csv(board, raw / f"{board.name}-bom.csv")

    files = [*gerber_files, *drill_files, pos_file, bom_file]
    zip_path = out_dir / f"{board.name}-{fab_target}.zip"
    _zip_for_target(files, zip_path, fab_target)

    # Mirror the loose files into out_dir/ for convenience.
    for f in files:
        shutil.copy2(f, out_dir / f.name)

    return FabPackage(directory=out_dir, zip_path=zip_path, fab_target=fab_target, files=files)


def _zip_for_target(files: list[Path], zip_path: Path, fab_target: str) -> None:
    """Write ``files`` into ``zip_path``.

    JLCPCB historically wants Gerber+drill at the top of the zip; the BOM
    and P&P CSVs upload separately. We still include them in the zip for
    self-containment.
    """
    _ = fab_target  # all targets share the same packing today; differentiate in M2
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
