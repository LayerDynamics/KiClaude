"""Placement algorithm tests."""

from __future__ import annotations

from ki_mcp_pcb_core.cir.models import Component
from ki_mcp_pcb_core.placement import grid_layout, parse_hint, plan_placement

# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


def test_grid_layout_empty() -> None:
    assert grid_layout([]) == []


def test_grid_layout_preserves_order() -> None:
    placements = grid_layout(["U1", "C1", "R1"])
    assert [p.refdes for p in placements] == ["U1", "C1", "R1"]


def test_grid_layout_uses_square_arrangement() -> None:
    placements = grid_layout(["A", "B", "C", "D", "E", "F", "G", "H", "I"], spacing_mm=10)
    # sqrt(9) = 3, so 3 cols × 3 rows; positions are deterministic
    xs = sorted({p.x_mm for p in placements})
    ys = sorted({p.y_mm for p in placements})
    assert len(xs) == 3
    assert len(ys) == 3


def test_grid_layout_matches_populator_spacing() -> None:
    """Python-side placement must match scripts/kicad_populate.py defaults."""
    placements = grid_layout(["A", "B", "C"], spacing_mm=15.0, margin_mm=20.0)
    # First component lands at (20, 20) — same as the pcbnew side
    assert placements[0].x_mm == 20.0
    assert placements[0].y_mm == 20.0


# ---------------------------------------------------------------------------
# Hint parser
# ---------------------------------------------------------------------------


def test_parse_hint_none_when_empty() -> None:
    assert parse_hint("") == ("none", ())
    assert parse_hint("   ") == ("none", ())


def test_parse_hint_edges() -> None:
    assert parse_hint("south edge")[0] == "south_edge"
    assert parse_hint("North edge, centered horizontally")[0] == "north_edge"
    assert parse_hint("EAST EDGE")[0] == "east_edge"


def test_parse_hint_centered() -> None:
    assert parse_hint("centered")[0] == "center"
    assert parse_hint("center")[0] == "center"


def test_parse_hint_within_mm_of_refdes() -> None:
    kind, args = parse_hint("within 2 mm of U1")
    assert kind == "near"
    assert args == ("2", "U1")


def test_parse_hint_freeform_for_unknown_pattern() -> None:
    """Raw coordinates from an LLM must NOT be interpreted as placement."""
    kind, _args = parse_hint("x=42.0 y=10.5")
    assert kind == "freeform"  # We treat as commentary, not coordinates


# ---------------------------------------------------------------------------
# plan_placement
# ---------------------------------------------------------------------------


def test_plan_placement_centers_when_hinted() -> None:
    comps = [Component(refdes="U1", mpn="X", placement_hint="centered")]
    placed = plan_placement(comps, board_width_mm=50, board_height_mm=40)
    assert placed[0].x_mm == 25.0
    assert placed[0].y_mm == 20.0


def test_plan_placement_pushes_south_edge() -> None:
    comps = [Component(refdes="J1", mpn="X", placement_hint="south edge")]
    placed = plan_placement(comps, board_width_mm=50, board_height_mm=40)
    assert placed[0].y_mm > 30.0  # near the bottom edge


def test_plan_placement_falls_back_to_grid_when_no_hint() -> None:
    comps = [
        Component(refdes="U1", mpn="A"),
        Component(refdes="C1", mpn="B"),
    ]
    placed = plan_placement(comps)
    assert {p.refdes for p in placed} == {"U1", "C1"}
