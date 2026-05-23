#!/usr/bin/env python3
"""Regenerate the golden CIR JSON Schema snapshot.

Run this *only* when you intend to change the CIR contract. Bump
``CIR_VERSION`` and add a migration in the same commit.

    uv run python tests/golden/regenerate.py
"""

from __future__ import annotations

import json
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board

OUT = Path(__file__).resolve().parent / "board_schema.json"


def main() -> int:
    schema = Board.model_json_schema()
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
