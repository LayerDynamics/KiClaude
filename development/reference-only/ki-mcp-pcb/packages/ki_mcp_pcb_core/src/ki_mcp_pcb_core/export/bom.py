"""BOM export.

Produces a fab-friendly CSV directly from the CIR — no KiCad dependency.
Per CLAUDE.md rule #6: every MPN must resolve, so the sourcing layer is
responsible for failing closed before this writer is called.
"""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel

from ki_mcp_pcb_core.cir.models import Board, Component


class BOMRow(BaseModel):
    """One line in the BOM CSV.

    Mirrors the JLCPCB-style columns: Comment, Designator, Footprint,
    LCSC, MPN. ``lcsc`` is filled by the sourcing layer when present.
    """

    comment: str  # human-readable description (often the value or MPN)
    designator: str  # comma-joined refdes group: "C1,C2,C3"
    footprint: str  # KiCad lib:fp identifier
    mpn: str
    lcsc: str | None = None
    quantity: int = 1


def build_bom_rows(board: Board) -> list[BOMRow]:
    """Group components by MPN+footprint+value into BOMRows."""
    groups: dict[tuple[str, str, str], list[Component]] = {}
    for comp in board.components:
        key = (comp.mpn, comp.footprint or "", comp.value or "")
        groups.setdefault(key, []).append(comp)

    rows: list[BOMRow] = []
    for (mpn, footprint, value), comps in sorted(groups.items(), key=lambda kv: kv[0][0]):
        designators = ",".join(sorted(c.refdes for c in comps))
        rows.append(
            BOMRow(
                comment=value or mpn,
                designator=designators,
                footprint=footprint,
                mpn=mpn,
                quantity=len(comps),
            )
        )
    return rows


def write_bom_csv(board: Board, out_path: Path) -> Path:
    """Write a JLC-flavored BOM CSV to ``out_path``."""
    rows = build_bom_rows(board)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Comment", "Designator", "Footprint", "MPN", "LCSC", "Quantity"])
        for row in rows:
            writer.writerow([
                row.comment,
                row.designator,
                row.footprint,
                row.mpn,
                row.lcsc or "",
                row.quantity,
            ])
    return out_path
