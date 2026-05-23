"""Round-trip fidelity tests.

Anything we can put in a ``Board`` we must be able to:
  - serialize to JSON (model_dump_json)
  - re-parse from JSON (model_validate_json)
  - serialize to dict, then back to Board
  - serialize to YAML and re-parse via parse_yaml

…and get an equal Board back. Failure here breaks the MCP layer (which
passes Boards as JSON over the wire) and the YAML parser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.parsers.yaml import parse_yaml


def _example_paths() -> list[Path]:
    here = Path(__file__).resolve().parents[1] / "examples"
    return sorted(here.glob("*.yaml"))


@pytest.mark.parametrize("path", _example_paths())
def test_yaml_to_board_to_dict_to_board(path: Path) -> None:
    board = parse_yaml(path)
    dumped = board.model_dump()
    revived = Board.model_validate(dumped)
    assert revived == board


@pytest.mark.parametrize("path", _example_paths())
def test_yaml_to_board_to_json_to_board(path: Path) -> None:
    board = parse_yaml(path)
    js = board.model_dump_json()
    revived = Board.model_validate_json(js)
    assert revived == board


@pytest.mark.parametrize("path", _example_paths())
def test_yaml_dump_roundtrip(path: Path) -> None:
    """Board → YAML text → Board, via parse_yaml."""
    board = parse_yaml(path)
    yaml_text = yaml.safe_dump(board.model_dump(), sort_keys=False)
    revived = parse_yaml(yaml_text)
    assert revived == board


def test_json_output_is_str_keyed() -> None:
    """Pydantic must hand us str-keyed JSON for MCP wire-compatibility."""
    board = parse_yaml(_example_paths()[0])
    js = board.model_dump_json()
    parsed = json.loads(js)
    assert isinstance(parsed, dict)
    assert all(isinstance(k, str) for k in parsed)
