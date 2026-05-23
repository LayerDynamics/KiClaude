"""Pytest entrypoint for the LLM eval harness.

Two modes:

  * **Self-tests** (always run): with a perfect parser, every fixture must
    pass; with a deliberately faulty parser, the judge must catch it.
    This guards the harness itself.
  * **Live LLM evals** (opt-in via ``ANTHROPIC_API_KEY``): the real
    ``parse_nl`` runs against every fixture and the judge scores it.
    Skipped when no API key is set.
"""

from __future__ import annotations

import os

import pytest

from .runner import (
    EvalResult,
    faulty_parser_factory,
    load_fixtures,
    perfect_parser_factory,
    run,
)


def _real_parser():
    """Return the live parse_nl-as-Parser. Importing here keeps the rest
    of the file usable when the SDK isn't installed."""
    from ki_mcp_pcb_core.parsers.nl import parse_nl

    def _adapter(prompt: str):
        return parse_nl(prompt).board

    return _adapter


def _summarize(results: list[EvalResult]) -> str:
    lines = []
    for r in results:
        marks = " ".join(
            f"{k}={v:.2f}" for k, v in r.scores.as_dict().items()
        )
        lines.append(f"  {r.fixture}: {'PASS' if r.passes else 'FAIL'}  {marks}")
        if r.failed_metrics:
            lines.append(f"    failed: {', '.join(r.failed_metrics)}")
    return "\n".join(lines)


def test_fixtures_exist() -> None:
    fixtures = load_fixtures()
    assert fixtures, "expected at least one fixture under tests/llm_evals/fixtures/"


def test_harness_passes_with_perfect_parser() -> None:
    """Sanity: a parser that always returns the expected Board must pass every
    fixture. If this fails, the JUDGE is broken — not the parser."""
    fixtures = load_fixtures()
    for fx in fixtures:
        parser = perfect_parser_factory(fx.expected)
        results = run(parser, fixtures=[fx])
        assert results[0].passes, (
            f"perfect parser failed fixture {fx.name!r}: "
            f"{results[0].failed_metrics}\n{_summarize(results)}"
        )


def test_harness_catches_dropped_component() -> None:
    """A parser that drops a component must fail the component_set metric."""
    fixtures = load_fixtures()
    fx = fixtures[0]  # any fixture with components will do
    assert fx.expected.components, "first fixture must have at least one component"

    parser = faulty_parser_factory(fx.expected, drop_first_component=True)
    results = run(parser, fixtures=[fx])
    assert not results[0].passes
    assert "component_set" in results[0].failed_metrics


def test_harness_catches_wrong_mpn() -> None:
    """A parser that swaps an MPN must fail mpn_exact (when threshold is strict)."""
    fixtures = load_fixtures()
    # find a fixture with strict mpn_exact threshold
    fx = next((f for f in fixtures if f.thresholds["mpn_exact"] >= 1.0), None)
    if fx is None:
        pytest.skip("no fixture with strict mpn_exact threshold")
    parser = faulty_parser_factory(fx.expected, wrong_mpn=True)
    results = run(parser, fixtures=[fx])
    assert not results[0].passes
    assert "mpn_exact" in results[0].failed_metrics


@pytest.mark.skipif(
    "ANTHROPIC_API_KEY" not in os.environ,
    reason="Set ANTHROPIC_API_KEY to run the live LLM eval suite.",
)
def test_real_parser_runs_when_available() -> None:  # pragma: no cover — live API
    """Run every fixture through the real ``parse_nl`` and judge it.

    Each fixture's thresholds gate pass/fail. Per-fixture failures are
    surfaced in the assertion message.
    """
    parser = _real_parser()
    results = run(parser)
    failed = [r for r in results if not r.passes]
    assert not failed, f"LLM eval failures:\n{_summarize(results)}"
