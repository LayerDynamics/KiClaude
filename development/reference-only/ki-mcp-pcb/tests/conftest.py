"""Shared pytest fixtures + helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.cir.models import Board, Component, Net

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
REAL_DESIGNS = Path(__file__).resolve().parent / "real_designs"
GOLDEN = Path(__file__).resolve().parent / "golden"


def _minimal_board() -> Board:
    """The reference healthy board used by many tests."""
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


@pytest.fixture
def minimal_board() -> Board:
    return _minimal_board()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture(scope="session")
def real_designs_dir() -> Path:
    return REAL_DESIGNS


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN
