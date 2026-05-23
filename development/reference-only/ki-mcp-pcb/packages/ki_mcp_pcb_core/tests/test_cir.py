"""Smoke tests for the CIR schema + structural validation."""

from __future__ import annotations

import pytest
from ki_mcp_pcb_core.cir.models import (
    CIR_VERSION,
    Board,
    Component,
    FabTarget,
    Net,
)
from ki_mcp_pcb_core.cir.validation import validate_board


def _minimal_board() -> Board:
    return Board(
        name="smoke",
        components=[
            Component(refdes="U1", mpn="ESP32-S3-WROOM-1"),
            Component(refdes="C1", mpn="GRM188R71C104KA01D", value="100nF"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "C1.2"]),
            Net(name="3V3", net_class="power", members=["U1.2", "C1.1"]),
        ],
    )


def test_cir_version_constant_present() -> None:
    assert CIR_VERSION == "0.4"


def test_minimal_board_validates() -> None:
    board = _minimal_board()
    report = validate_board(board)
    assert report.ok, report.errors


def test_duplicate_refdes_caught() -> None:
    board = _minimal_board()
    board.components.append(Component(refdes="U1", mpn="DUPLICATE"))
    report = validate_board(board)
    assert not report.ok
    assert any(i.code == "CIR001" for i in report.errors)


def test_unknown_net_member_caught() -> None:
    board = _minimal_board()
    board.nets[0].members.append("U99.1")
    report = validate_board(board)
    assert not report.ok
    assert any(i.code == "CIR002" for i in report.errors)


def test_stackup_layer_count_must_match_fab() -> None:
    board = _minimal_board()
    board.fab = FabTarget(layer_count=4)  # stackup is still 2-layer
    report = validate_board(board)
    assert not report.ok
    assert any(i.code == "CIR020" for i in report.errors)


def test_refdes_pattern_enforced() -> None:
    with pytest.raises(ValueError):
        Component(refdes="lowercase1", mpn="X")
