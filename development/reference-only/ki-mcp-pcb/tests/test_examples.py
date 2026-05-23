"""End-to-end: every example YAML in examples/ validates clean."""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.cir.validation import validate_board
from ki_mcp_pcb_core.parsers.yaml import parse_yaml

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.yaml")))
def test_example_validates(path: Path) -> None:
    board = parse_yaml(path)
    report = validate_board(board)
    assert report.ok, f"{path.name} failed:\n" + "\n".join(
        f"  {i.code} {i.where or ''} {i.message}" for i in report.errors
    )
