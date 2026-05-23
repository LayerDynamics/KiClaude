"""Length-tuning queue analyzer (M3 CIR080-flavored).

Takes the JSON output of ``scripts/kicad_measure_lengths.py`` plus the
CIR ``Board`` and produces:

  * a per-group summary (longest member, target, tolerance, deviation)
  * a per-net "needs adjustment" queue (which nets are short by N mm)

Works as a pure function over data — no subprocess, no KiCad. The
subprocess wrapper that runs the measurement script lives in
:mod:`ki_mcp_pcb_core.synthesis.populator` style; this module is the
*math*.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from ki_mcp_pcb_core.cir.models import Board, Constraint


class Measurement(BaseModel):
    length_mm: float
    trace_count: int


@dataclass(frozen=True)
class GroupReport:
    group: str
    target_mm: float            # longest member sets the target
    tolerance_mm: float         # absolute tolerance derived from constraint
    nets: dict[str, float]      # net_name -> length_mm
    in_tolerance: bool


@dataclass(frozen=True)
class TuningAction:
    """A specific net needs to be made longer (or shorter) by ``delta_mm``."""

    net: str
    group: str
    current_mm: float
    target_mm: float
    delta_mm: float
    direction: str  # "lengthen" or "shorten"


@dataclass(frozen=True)
class TuningReport:
    groups: list[GroupReport]
    queue: list[TuningAction]

    @property
    def ok(self) -> bool:
        return all(g.in_tolerance for g in self.groups)


def parse_measurements(report_path: Path) -> dict[str, Measurement]:
    """Load ``scripts/kicad_measure_lengths.py`` output."""
    import json
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))
    return {name: Measurement.model_validate(entry) for name, entry in data.get("nets", {}).items()}


def _tolerance_for_group(board: Board, group: str, default_pct: float = 5.0) -> tuple[float, str]:
    """Find the constraint tolerance for ``group``. Returns (tolerance_pct, mode)."""
    for c in board.constraints:
        if c.kind == "length_match" and group in c.targets:
            if c.tolerance_pct is not None:
                return c.tolerance_pct, "percent"
            if c.value_mm is not None:
                return c.value_mm, "absolute_mm"
    return default_pct, "percent"


def analyze_tuning(board: Board, measurements: dict[str, Measurement]) -> TuningReport:
    """Build the length-tuning report from a CIR + measured lengths."""
    # Group nets by length_match_group
    groups: dict[str, list[str]] = {}
    for net in board.nets:
        if net.length_match_group:
            groups.setdefault(net.length_match_group, []).append(net.name)

    group_reports: list[GroupReport] = []
    queue: list[TuningAction] = []

    for group_name, net_names in groups.items():
        lengths = {n: measurements[n].length_mm for n in net_names if n in measurements}
        if not lengths:
            continue
        target = max(lengths.values())
        tol_value, tol_mode = _tolerance_for_group(board, group_name)
        tolerance_mm = target * (tol_value / 100.0) if tol_mode == "percent" else tol_value

        in_tol = True
        for net_name, length_mm in lengths.items():
            delta = target - length_mm  # positive means net is short
            if abs(delta) > tolerance_mm:
                in_tol = False
                queue.append(TuningAction(
                    net=net_name,
                    group=group_name,
                    current_mm=round(length_mm, 4),
                    target_mm=round(target, 4),
                    delta_mm=round(delta, 4),
                    direction="lengthen" if delta > 0 else "shorten",
                ))

        group_reports.append(GroupReport(
            group=group_name,
            target_mm=round(target, 4),
            tolerance_mm=round(tolerance_mm, 4),
            nets={n: round(v, 4) for n, v in lengths.items()},
            in_tolerance=in_tol,
        ))

    return TuningReport(groups=group_reports, queue=queue)


def constraint_for(board: Board, group: str) -> Constraint | None:
    """Lookup helper used by tests and the CLI's tuning command."""
    for c in board.constraints:
        if c.kind == "length_match" and group in c.targets:
            return c
    return None
