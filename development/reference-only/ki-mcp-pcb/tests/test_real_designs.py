"""Real-design CIR validation.

Each YAML in ``tests/real_designs/`` is a hand-authored CIR for a
shipping reference design. They double as a schema-completeness
audit — anything you can't model is a gap. Errors fail the test;
warnings (e.g. CIR010 ground-not-found) are surfaced but don't fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.cir.validation import validate_board
from ki_mcp_pcb_core.parsers.yaml import parse_yaml

REAL = Path(__file__).resolve().parent / "real_designs"


@pytest.mark.parametrize("path", sorted(REAL.glob("*.yaml")))
def test_real_design_validates(path: Path) -> None:
    board = parse_yaml(path)
    report = validate_board(board)
    assert report.ok, (
        f"{path.name} validation errors:\n"
        + "\n".join(f"  {i.code} {i.where or ''} {i.message}" for i in report.errors)
    )


@pytest.mark.parametrize("path", sorted(REAL.glob("*.yaml")))
def test_real_design_has_ground_net(path: Path) -> None:
    """Every real design must declare a ground net — otherwise the schema
    is too permissive for production use."""
    board = parse_yaml(path)
    assert any(n.net_class == "ground" for n in board.nets), (
        f"{path.name} has no ground net — schema/example mismatch"
    )
