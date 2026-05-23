"""Rendering: schematic / board → PNG / SVG.

Used by the MCP layer to hand Claude Code a visual preview after each
step. Wraps ``kicad-cli`` plot commands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

View = Literal["schematic", "board_top", "board_bottom", "3d"]


def render(_source: Path, view: View = "board_top", _out: Path | None = None) -> Path:
    """Render a KiCad source file to PNG. Returns the PNG path."""
    _ = view
    raise NotImplementedError("render() lands in M1.")
