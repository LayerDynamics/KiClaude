"""UI-only kc tools (M1-P-05).

These functions exist solely to back the React frontend's drag-drop
and property-panel surfaces. Per [SPEC §1.4 principle #4][spec],
they take raw screen coordinates and MUST NOT be registered with
the Claude MCP server — the
[`assert_no_ui_tools_in_claude_registry`][assert_no_ui_tools_in_claude_registry]
guard in [`kc_mcp.server`] enforces this at boot.

[spec]: ../../../../../docs/specs/SPEC-01-kiclaude.md
"""

from __future__ import annotations

from .footprint_move import ui_footprint_move
from .footprint_xy import ui_footprint_place_xy
from .junction_xy import ui_junction_place_xy
from .label_xy import ui_label_place_xy
from .symbol_edit import ui_symbol_edit_props
from .symbol_xy import ui_symbol_place_xy
from .track_points import ui_track_draw_points
from .via_xy import ui_via_place_xy
from .wire_points import ui_wire_draw_points
from .zone_polygon import ui_zone_create_polygon

UI_TOOLS = {
    # M1-P-05 schematic UI tools.
    "ui_symbol_place_xy": ui_symbol_place_xy,
    "ui_wire_draw_points": ui_wire_draw_points,
    "ui_label_place_xy": ui_label_place_xy,
    "ui_junction_place_xy": ui_junction_place_xy,
    "ui_symbol_edit_props": ui_symbol_edit_props,
    # M2-P-05 PCB UI tools (5).
    "ui_footprint_place_xy": ui_footprint_place_xy,
    "ui_footprint_move": ui_footprint_move,
    "ui_track_draw_points": ui_track_draw_points,
    "ui_via_place_xy": ui_via_place_xy,
    "ui_zone_create_polygon": ui_zone_create_polygon,
}


__all__ = [
    "UI_TOOLS",
    "ui_footprint_move",
    "ui_footprint_place_xy",
    "ui_junction_place_xy",
    "ui_label_place_xy",
    "ui_symbol_edit_props",
    "ui_symbol_place_xy",
    "ui_track_draw_points",
    "ui_via_place_xy",
    "ui_wire_draw_points",
    "ui_zone_create_polygon",
]
