"""YAML/TOML → CIR.

The deterministic parser. Reads a CIR document straight from YAML.
Useful as a debugging format and as the synthesized canonical form
when round-tripping from an ``.ato`` file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ki_mcp_pcb_core.cir.migrations import upgrade
from ki_mcp_pcb_core.cir.models import Board


def parse_yaml(source: str | Path) -> Board:
    """Parse a YAML CIR document into a ``Board``.

    Accepts either a string (the YAML text itself) or a path to a file.
    Runs CIR migrations automatically.
    """
    text = _load_text(source)
    raw: Any = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("YAML root must be a mapping")
    raw = upgrade(raw)
    return Board.model_validate(raw)


def _load_text(source: str | Path) -> str:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    # Heuristic: if it looks like a path and the file exists, read it.
    if "\n" not in source and Path(source).exists():
        return Path(source).read_text(encoding="utf-8")
    return source
