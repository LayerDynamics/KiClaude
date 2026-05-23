"""Semantic-equivalence judge for NL → CIR evals.

Each metric returns a float in [0.0, 1.0]; 1.0 is identical. Fixtures
declare thresholds so a slightly-loose match doesn't fail a fuzzy prompt
but a 'forgot the MCU' answer does.
"""

from __future__ import annotations

from dataclasses import dataclass

from ki_mcp_pcb_core.cir.models import Board, Component, Constraint, Net


@dataclass(frozen=True)
class JudgmentScores:
    component_set: float
    mpn_exact: float
    net_class: float
    net_membership: float
    constraints: float
    fab_target: float

    def as_dict(self) -> dict[str, float]:
        return {
            "component_set": self.component_set,
            "mpn_exact": self.mpn_exact,
            "net_class": self.net_class,
            "net_membership": self.net_membership,
            "constraints": self.constraints,
            "fab_target": self.fab_target,
        }


# ---------------------------------------------------------------------------
# Individual metrics
# ---------------------------------------------------------------------------


def _set_jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    inter = a & b
    return len(inter) / len(union) if union else 1.0


def score_component_set(expected: list[Component], actual: list[Component]) -> float:
    """How well do the refdes sets match?"""
    return _set_jaccard({c.refdes for c in expected}, {c.refdes for c in actual})


def score_mpn_exact(expected: list[Component], actual: list[Component]) -> float:
    """For each refdes both sides have, is the MPN identical? Reported as
    a fraction of the intersection."""
    exp = {c.refdes: c.mpn for c in expected}
    act = {c.refdes: c.mpn for c in actual}
    shared = set(exp) & set(act)
    if not shared:
        return 1.0 if not exp and not act else 0.0
    matches = sum(1 for r in shared if exp[r] == act[r])
    return matches / len(shared)


def score_net_class(expected: list[Net], actual: list[Net]) -> float:
    """For nets present on both sides, does the class match?"""
    exp = {n.name: n.net_class for n in expected}
    act = {n.name: n.net_class for n in actual}
    shared = set(exp) & set(act)
    if not shared:
        return 1.0 if not exp and not act else 0.0
    matches = sum(1 for n in shared if exp[n] == act[n])
    return matches / len(shared)


def score_net_membership(expected: list[Net], actual: list[Net]) -> float:
    """Jaccard over (net_name → set-of-members), averaged across nets."""
    exp = {n.name: set(n.members) for n in expected}
    act = {n.name: set(n.members) for n in actual}
    all_names = set(exp) | set(act)
    if not all_names:
        return 1.0
    scores = [_set_jaccard(exp.get(name, set()), act.get(name, set())) for name in all_names]
    return sum(scores) / len(scores)


def score_constraints(expected: list[Constraint], actual: list[Constraint]) -> float:
    """Compare (kind, frozenset(targets)) tuples as sets."""

    def key(c: Constraint) -> tuple[str, frozenset[str]]:
        return (c.kind, frozenset(c.targets))

    return _set_jaccard({key(c) for c in expected}, {key(c) for c in actual})


def score_fab_target(expected: Board, actual: Board) -> float:
    """1.0 if same fab + same layer count, else 0.0."""
    return float(
        expected.fab.name == actual.fab.name
        and expected.fab.layer_count == actual.fab.layer_count
    )


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def judge(expected: Board, actual: Board) -> JudgmentScores:
    """Score a candidate Board against an expected one."""
    return JudgmentScores(
        component_set=score_component_set(expected.components, actual.components),
        mpn_exact=score_mpn_exact(expected.components, actual.components),
        net_class=score_net_class(expected.nets, actual.nets),
        net_membership=score_net_membership(expected.nets, actual.nets),
        constraints=score_constraints(expected.constraints, actual.constraints),
        fab_target=score_fab_target(expected, actual),
    )
