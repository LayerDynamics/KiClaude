"""Property-based tests on the CIR.

Hypothesis generates randomized ``Board`` instances and we assert
invariants that must hold for *any* well-formed board. Catches the
edge cases hand-authored unit tests miss.

If hypothesis isn't installed, these tests are skipped — they're a
nice-to-have at M0, not a release gate.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from ki_mcp_pcb_core.cir.models import (  # noqa: E402
    Board,
    Component,
    Net,
)
from ki_mcp_pcb_core.cir.validation import validate_board  # noqa: E402

# Strategies ----------------------------------------------------------------

refdes_prefix = st.sampled_from(["R", "C", "L", "D", "Q", "U", "J", "Y", "F", "TP"])
refdes_num = st.integers(min_value=1, max_value=99)


@st.composite
def refdes_st(draw: st.DrawFn) -> str:
    prefix = draw(refdes_prefix)
    num = draw(refdes_num)
    return f"{prefix}{num}"


@st.composite
def component_st(draw: st.DrawFn, used: set[str] | None = None) -> Component:
    used = used or set()
    for _ in range(10):
        refdes = draw(refdes_st())
        if refdes not in used:
            used.add(refdes)
            break
    return Component(
        refdes=refdes,
        mpn=draw(st.text(min_size=1, max_size=24, alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="\"'"))),
    )


@st.composite
def board_st(draw: st.DrawFn) -> Board:
    n = draw(st.integers(min_value=0, max_value=8))
    used: set[str] = set()
    comps = [draw(component_st(used)) for _ in range(n)]
    refdes_list = [c.refdes for c in comps]

    # Build nets that only reference real components.
    net_count = draw(st.integers(min_value=0, max_value=4))
    nets = []
    for i in range(net_count):
        if not refdes_list:
            members: list[str] = []
        else:
            members = [
                f"{draw(st.sampled_from(refdes_list))}.{draw(st.integers(min_value=1, max_value=8))}"
                for _ in range(draw(st.integers(min_value=1, max_value=4)))
            ]
        nets.append(Net(name=f"NET{i}", members=members))

    # Always include GND so we don't trip the CIR010 warning every run.
    nets.append(Net(name="GND", net_class="ground",
                    members=[f"{refdes_list[0]}.1"] if refdes_list else []))

    return Board(name="hyp", components=comps, nets=nets)


# Properties ----------------------------------------------------------------


@given(board_st())
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_validation_is_total(board: Board) -> None:
    """validate_board must terminate and produce a report for ANY valid Board."""
    report = validate_board(board)
    assert report is not None
    assert isinstance(report.issues, list)


@given(board_st())
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_validation_is_deterministic(board: Board) -> None:
    """Same board → same report. No hidden state, no random codepaths."""
    a = validate_board(board).model_dump()
    b = validate_board(board).model_dump()
    assert a == b


@given(board_st())
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_roundtrip_dict_identity(board: Board) -> None:
    """Board → dict → Board must equal the original."""
    revived = Board.model_validate(board.model_dump())
    assert revived == board


@given(board_st())
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_unique_refdes_means_no_cir001(board: Board) -> None:
    """When refdes are unique by construction, CIR001 must NOT fire."""
    seen: set[str] = set()
    has_dupe = False
    for c in board.components:
        if c.refdes in seen:
            has_dupe = True
            break
        seen.add(c.refdes)
    if has_dupe:
        return  # property only states the silent direction
    issues = validate_board(board).issues
    assert not any(i.code == "CIR001" for i in issues)
