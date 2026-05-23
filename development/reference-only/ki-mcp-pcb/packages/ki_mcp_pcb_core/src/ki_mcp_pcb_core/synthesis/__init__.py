"""Synthesis: CIR → KiCad project skeleton.

Callers hand in a validated ``Board`` and an output directory; we
emit ``.kicad_pro`` + ``.net`` (netlist) + ``.kicad_pcb`` (empty
skeleton). Component placement happens via KiCad's "Update PCB from
netlist" — either through the GUI (manual M1 step) or pcbnew's Python
API (M2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board


@dataclass(frozen=True)
class SynthesisResult:
    project_path: Path  # .kicad_pro
    pcb_path: Path  # .kicad_pcb
    schematic_path: Path  # .kicad_sch
    netlist_path: Path  # .net


def synthesize(board: Board, out_dir: Path) -> SynthesisResult:
    """Generate KiCad project skeleton from a Board.

    Fails closed via :class:`~ki_mcp_pcb_core.synthesis.resolver.UnresolvedMPNError`
    if any component MPN can't be resolved.
    """
    # Lazy import avoids a backends.kicad ↔ synthesis circular dependency.
    from ki_mcp_pcb_core.backends.kicad import KiCadBackend

    out_dir = Path(out_dir)
    pro_path = KiCadBackend().write_project(board, out_dir)
    return SynthesisResult(
        project_path=pro_path,
        pcb_path=out_dir / f"{board.name}.kicad_pcb",
        schematic_path=out_dir / f"{board.name}.kicad_sch",
        netlist_path=out_dir / f"{board.name}.net",
    )
