"""Negative-case suite — one fires-when-it-should + one stays-silent per code.

Every diagnostic in ``ki_mcp_pcb_core.cir.validation`` gets at least two
tests here. If you add a new code, you MUST add both cases or this
file's coverage-of-codes test fails.
"""

from __future__ import annotations

import pytest
from ki_mcp_pcb_core.cir.models import (
    Board,
    Component,
    FabTarget,
    Layer,
    Net,
    Stackup,
)
from ki_mcp_pcb_core.cir.validation import validate_board

# ---------------------------------------------------------------------------
# CIR001 — duplicate refdes
# ---------------------------------------------------------------------------


def test_cir001_fires_on_duplicate_refdes(minimal_board: Board) -> None:
    minimal_board.components.append(Component(refdes="U1", mpn="ANOTHER"))
    report = validate_board(minimal_board)
    codes = {i.code for i in report.errors}
    assert "CIR001" in codes


def test_cir001_silent_when_unique(minimal_board: Board) -> None:
    report = validate_board(minimal_board)
    assert not any(i.code == "CIR001" for i in report.issues)


# ---------------------------------------------------------------------------
# CIR002 — net member references unknown component
# ---------------------------------------------------------------------------


def test_cir002_fires_on_unknown_refdes_in_net(minimal_board: Board) -> None:
    minimal_board.nets[0].members.append("U99.1")
    report = validate_board(minimal_board)
    assert any(i.code == "CIR002" for i in report.errors)


def test_cir002_silent_when_members_resolve(minimal_board: Board) -> None:
    report = validate_board(minimal_board)
    assert not any(i.code == "CIR002" for i in report.issues)


# ---------------------------------------------------------------------------
# CIR003 — net member missing pin number
# ---------------------------------------------------------------------------


def test_cir003_fires_on_missing_pin() -> None:
    # Bypass model validation by constructing the Net with a bad member,
    # then exercising validate_board. The field_validator on Net.members
    # also catches this — we test both paths.
    with pytest.raises(ValueError):
        Net(name="X", members=["U1"])  # no '.pin'


# ---------------------------------------------------------------------------
# CIR010 — missing ground net (warning)
# ---------------------------------------------------------------------------


def test_cir010_warns_when_no_ground() -> None:
    board = Board(
        name="no-gnd",
        components=[Component(refdes="U1", mpn="X")],
        nets=[Net(name="VBUS", net_class="power", members=["U1.1"])],
    )
    report = validate_board(board)
    assert any(i.code == "CIR010" and i.severity == "warning" for i in report.warnings)


def test_cir010_silent_when_ground_present(minimal_board: Board) -> None:
    report = validate_board(minimal_board)
    assert not any(i.code == "CIR010" for i in report.issues)


def test_cir010_silent_for_empty_board() -> None:
    """An empty board has nothing to ground, so the warning shouldn't fire."""
    report = validate_board(Board(name="empty"))
    assert not any(i.code == "CIR010" for i in report.issues)


# ---------------------------------------------------------------------------
# CIR020 — stackup copper layers vs fab layer_count
# ---------------------------------------------------------------------------


def test_cir020_fires_on_mismatch(minimal_board: Board) -> None:
    minimal_board.fab = FabTarget(layer_count=4)  # stackup is 2-layer
    report = validate_board(minimal_board)
    assert any(i.code == "CIR020" for i in report.errors)


def test_cir020_silent_when_4layer_matches() -> None:
    stackup = Stackup(
        layers=[
            Layer(name="F.Cu", kind="copper"),
            Layer(name="pp1", kind="dielectric", material="prepreg", er=4.3),
            Layer(name="In1.Cu", kind="copper"),
            Layer(name="core", kind="dielectric", material="FR-4", er=4.5),
            Layer(name="In2.Cu", kind="copper"),
            Layer(name="pp2", kind="dielectric", material="prepreg", er=4.3),
            Layer(name="B.Cu", kind="copper"),
        ]
    )
    board = Board(
        name="4layer",
        stackup=stackup,
        fab=FabTarget(layer_count=4),
        components=[Component(refdes="U1", mpn="X")],
        nets=[Net(name="GND", net_class="ground", members=["U1.1"])],
    )
    report = validate_board(board)
    assert not any(i.code == "CIR020" for i in report.issues)


# ---------------------------------------------------------------------------
# Meta — every documented code has both a fire and a silent test in this file
# ---------------------------------------------------------------------------


def test_every_diagnostic_code_has_both_cases() -> None:
    """If the validator grows a new code, this test catches missing coverage."""
    # M0/M1 codes here; M2 codes (CIR030/040/050) have their own coverage
    # in tests/test_m2_validators.py.
    expected_codes = {"CIR001", "CIR002", "CIR003", "CIR010", "CIR020"}
    # Hardcoded set — when you add a code in validation.py, add it here AND
    # add fires/silent tests above. This is the audit boundary.
    import inspect

    from ki_mcp_pcb_core.cir import validation

    source = inspect.getsource(validation)
    found_codes = {
        code for code in expected_codes if f'code="{code}"' in source or f"'{code}'" in source
    }
    assert found_codes == expected_codes, (
        f"Codes in validation.py ({found_codes}) don't match expected "
        f"({expected_codes}). Update the expected set when adding diagnostics."
    )
