"""Parsers: text → CIR.

Three tiers (see SPEC.md §4):
  - natural language (LLM-backed; lives in nl.py)
  - .ato DSL (wraps atopile compiler; lives in ato.py)
  - YAML/TOML (deterministic; lives in yaml.py)

All parsers return a ``Board`` from ``ki_mcp_pcb_core.cir.models``.
"""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.parsers.ato import parse_ato
from ki_mcp_pcb_core.parsers.yaml import parse_yaml

__all__ = ["parse_any", "parse_ato", "parse_yaml"]


def parse_any(source: str | Path) -> Board:
    """Dispatch on file extension. Convenience helper for CLI/MCP layers."""
    path = Path(source) if isinstance(source, str) and "\n" not in source else source
    if not isinstance(path, Path):
        # raw inline string heuristic — treat as YAML
        return parse_yaml(source)
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return parse_yaml(path)
    if suffix == ".ato":
        return parse_ato(path)
    raise ValueError(f"Unknown CIR source extension: {suffix!r}")
