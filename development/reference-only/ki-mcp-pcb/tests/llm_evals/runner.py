"""LLM eval runner.

Loads fixtures, runs them through a parser, judges the output. Parser is
injected so we can stand up the harness with a mock today and swap in the
real ``parse_nl`` when M1 lands.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ki_mcp_pcb_core.cir.models import Board

from .judge import JudgmentScores, judge

Parser = Callable[[str], Board]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class Fixture:
    name: str
    prompt: str
    expected: Board
    thresholds: dict[str, float]
    notes: str = ""


def _default_thresholds() -> dict[str, float]:
    """Strict by default. Per-fixture loosen via thresholds in the JSON file."""
    return {
        "component_set": 1.0,
        "mpn_exact": 1.0,
        "net_class": 1.0,
        "net_membership": 1.0,
        "constraints": 1.0,
        "fab_target": 1.0,
    }


def load_fixtures(directory: Path | None = None) -> list[Fixture]:
    """Load every ``*.json`` under fixtures/ (except _defaults.json)."""
    directory = directory or FIXTURES_DIR
    fixtures: list[Fixture] = []
    for path in sorted(directory.glob("*.json")):
        if path.stem.startswith("_"):
            continue
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        thresholds = _default_thresholds()
        thresholds.update(data.get("thresholds", {}))
        fixtures.append(
            Fixture(
                name=path.stem,
                prompt=data["prompt"],
                expected=Board.model_validate(data["expected_cir"]),
                thresholds=thresholds,
                notes=data.get("notes", ""),
            )
        )
    return fixtures


@dataclass(frozen=True)
class EvalResult:
    fixture: str
    scores: JudgmentScores
    passes: bool
    failed_metrics: list[str]


def run(parser: Parser, fixtures: list[Fixture] | None = None) -> list[EvalResult]:
    fixtures = fixtures or load_fixtures()
    results: list[EvalResult] = []
    for fx in fixtures:
        actual = parser(fx.prompt)
        scores = judge(fx.expected, actual)
        failed = [
            metric
            for metric, score in scores.as_dict().items()
            if score < fx.thresholds[metric]
        ]
        results.append(EvalResult(fixture=fx.name, scores=scores, passes=not failed,
                                  failed_metrics=failed))
    return results


# ---------------------------------------------------------------------------
# Mock parsers for harness self-tests
# ---------------------------------------------------------------------------


def perfect_parser_factory(expected: Board) -> Parser:
    """A parser that always returns the expected Board — used to confirm
    a passing fixture produces a passing result. Exists only for the
    harness self-test."""
    def _parse(_prompt: str) -> Board:
        return expected
    return _parse


def faulty_parser_factory(expected: Board, *, drop_first_component: bool = False,
                          wrong_mpn: bool = False) -> Parser:
    """A parser that deliberately mangles the expected Board to verify
    the judge catches mistakes."""
    def _parse(_prompt: str) -> Board:
        out = expected.model_copy(deep=True)
        if drop_first_component and out.components:
            out.components.pop(0)
        if wrong_mpn and out.components:
            out.components[0].mpn = "WRONG-PART-001"
        return out
    return _parse
