"""Placement: position components on the board.

Two complementary surfaces:

  * :func:`grid_layout` — pure function over a list of refdes, returns
    (x_mm, y_mm) for each. Used by tests, the M2 placement quality
    metrics, and as the CIR-side source of truth for "where would we
    place things if pcbnew weren't around to do it."

  * :func:`plan_placement` — apply declarative LLM hints
    (Component.placement_hint) on top of a base grid. Hints are
    constrained to known shapes ("south edge", "centered",
    "within 2mm of <refdes>"); raw coordinates from an LLM are
    explicitly NOT supported (CLAUDE.md rule #5).

The pcbnew populator does the actual placement on the .kicad_pcb. This
module is what the rest of the toolchain reasons about.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from ki_mcp_pcb_core.cir.models import Component


@dataclass(frozen=True)
class Placement:
    refdes: str
    x_mm: float
    y_mm: float
    rotation_deg: float = 0.0
    layer: str = "F.Cu"


# ---------------------------------------------------------------------------
# Grid layout
# ---------------------------------------------------------------------------


def grid_layout(
    refdes_list: Iterable[str],
    *,
    spacing_mm: float = 15.0,
    margin_mm: float = 20.0,
) -> list[Placement]:
    """Lay out components on a tight square-ish grid.

    Order is preserved from the input. Spacing defaults match the
    pcbnew script (``scripts/kicad_populate.py``) so both place
    components in the same locations for any given input ordering.
    """
    refdes = list(refdes_list)
    if not refdes:
        return []
    cols = max(1, math.ceil(math.sqrt(len(refdes))))
    placements: list[Placement] = []
    for i, name in enumerate(refdes):
        row, col = divmod(i, cols)
        placements.append(Placement(
            refdes=name,
            x_mm=col * spacing_mm + margin_mm,
            y_mm=row * spacing_mm + margin_mm,
        ))
    return placements


# ---------------------------------------------------------------------------
# Hint-aware planner
# ---------------------------------------------------------------------------

_HINT_PATTERNS = (
    (re.compile(r"^\s*south edge", re.I), "south_edge"),
    (re.compile(r"^\s*north edge", re.I), "north_edge"),
    (re.compile(r"^\s*east edge", re.I),  "east_edge"),
    (re.compile(r"^\s*west edge", re.I),  "west_edge"),
    (re.compile(r"^\s*center(?:ed)?\b", re.I),  "center"),
    (re.compile(r"^\s*within\s+([\d.]+)\s*mm\s+of\s+([A-Z]+[0-9]+)", re.I), "near"),
)


def parse_hint(hint: str) -> tuple[str, tuple[object, ...]]:
    """Parse a declarative placement hint. Returns ``(kind, args)``.

    Unknown hints map to ``("freeform", (hint,))`` so callers can choose
    to log or ignore them. We never let an LLM-supplied raw coordinate
    string through — anything that doesn't match a known pattern is
    treated as freeform commentary.
    """
    if not hint or not hint.strip():
        return ("none", ())
    for pattern, kind in _HINT_PATTERNS:
        m = pattern.match(hint)
        if not m:
            continue
        return (kind, m.groups())
    return ("freeform", (hint,))


def plan_placement(
    components: Iterable[Component],
    *,
    board_width_mm: float = 50.0,
    board_height_mm: float = 40.0,
    spacing_mm: float = 15.0,
) -> list[Placement]:
    """Apply hints over a grid layout. Pure function; hints are advisory."""
    comps = list(components)
    base = {p.refdes: p for p in grid_layout(
        [c.refdes for c in comps], spacing_mm=spacing_mm
    )}
    out: list[Placement] = []
    for comp in comps:
        p = base[comp.refdes]
        kind, _args = parse_hint(comp.placement_hint or "")
        if kind == "south_edge":
            p = Placement(refdes=p.refdes, x_mm=board_width_mm / 2, y_mm=board_height_mm - 2.0)
        elif kind == "north_edge":
            p = Placement(refdes=p.refdes, x_mm=board_width_mm / 2, y_mm=2.0)
        elif kind == "east_edge":
            p = Placement(refdes=p.refdes, x_mm=board_width_mm - 2.0, y_mm=board_height_mm / 2)
        elif kind == "west_edge":
            p = Placement(refdes=p.refdes, x_mm=2.0, y_mm=board_height_mm / 2)
        elif kind == "center":
            p = Placement(refdes=p.refdes, x_mm=board_width_mm / 2, y_mm=board_height_mm / 2)
        # "near" requires another component already placed; in M1 we ignore
        # ordering and treat it as a noop. M2 will resolve hint dependencies.
        out.append(p)
    return out
