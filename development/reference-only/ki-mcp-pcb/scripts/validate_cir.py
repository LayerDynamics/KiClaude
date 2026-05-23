#!/usr/bin/env python3
"""Standalone CIR validator.

Run a CIR YAML file through structural validation and print a report.
Exit code: 0 if no errors, 1 if any errors. Warnings do not fail.

Usage:
    uv run python scripts/validate_cir.py examples/blinky.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ki_mcp_pcb_core.cir.validation import validate_board
from ki_mcp_pcb_core.parsers.yaml import parse_yaml


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a CIR YAML file.")
    parser.add_argument("source", type=Path, help="Path to .yaml/.yml CIR file")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"error: file not found: {args.source}", file=sys.stderr)
        return 2

    board = parse_yaml(args.source)
    report = validate_board(board)

    if args.json:
        print(json.dumps(report.model_dump(), indent=2))
    else:
        for issue in report.issues:
            where = f" [{issue.where}]" if issue.where else ""
            print(f"{issue.severity:8s} {issue.code:8s}{where} {issue.message}")
        if not report.issues:
            print("ok — no issues")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
