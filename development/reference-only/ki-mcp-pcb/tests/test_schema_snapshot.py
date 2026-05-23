"""JSON Schema snapshot test.

CIR is the contract (CLAUDE.md rule #1). Schema changes must be visible
in a diff — never silent. If you see this test fail:

  1. Decide whether the schema change is intentional.
  2. If yes: bump ``CIR_VERSION`` and add a migration in
     ``ki_mcp_pcb_core.cir.migrations``, then regenerate the golden file:
     ``uv run python tests/golden/regenerate.py``
  3. If no: revert the schema change.

Never blindly regenerate to make the test pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board

GOLDEN = Path(__file__).resolve().parent / "golden" / "board_schema.json"


def _current_schema_json() -> str:
    return json.dumps(Board.model_json_schema(), indent=2, sort_keys=True)


def test_board_schema_matches_golden() -> None:
    expected = GOLDEN.read_text(encoding="utf-8").strip()
    actual = _current_schema_json().strip()
    assert actual == expected, (
        "CIR JSON schema drifted from the golden file. If the change is "
        "intentional, regenerate via `uv run python tests/golden/regenerate.py` "
        "and bump CIR_VERSION + add a migration. If not, revert the model change."
    )
