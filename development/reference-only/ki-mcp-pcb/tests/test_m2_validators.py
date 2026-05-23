"""Negative + positive cases for M2 design-intent validators.

Pattern matches tests/test_cir_negative.py: every code gets a "fires
when it should" + "stays silent when it shouldn't" pair.
"""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core.cir.models import (
    Board,
    Component,
    Constraint,
    FabTarget,
    Net,
    Stackup,
)
from ki_mcp_pcb_core.cir.validation import validate_board

# ---------------------------------------------------------------------------
# CIR030 — decoupling coverage
# ---------------------------------------------------------------------------


def test_cir030_fires_when_ic_has_no_decoupling_cap() -> None:
    board = Board(
        name="no-decoupling",
        components=[
            Component(refdes="U1", mpn="STM32F407VGT6", decoupling_pins=["11"]),
            # No caps at all
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.10"]),
            Net(name="3V3", net_class="power", power_rail="3V3", members=["U1.11"]),
        ],
    )
    report = validate_board(board)
    assert any(i.code == "CIR030" for i in report.errors)


def test_cir030_silent_when_decoupling_present() -> None:
    board = Board(
        name="ok-decoupling",
        components=[
            Component(refdes="U1", mpn="STM32F407VGT6", decoupling_pins=["11"]),
            Component(refdes="C1", mpn="GRM188R71C104KA01D"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.10", "C1.2"]),
            Net(name="3V3", net_class="power", power_rail="3V3", members=["U1.11", "C1.1"]),
        ],
    )
    report = validate_board(board)
    assert not any(i.code == "CIR030" for i in report.issues)


def test_cir030_silent_when_no_decoupling_pins_declared() -> None:
    """Components without declared decoupling_pins don't trigger the check."""
    board = Board(
        name="no-decl",
        components=[Component(refdes="U1", mpn="X")],
        nets=[Net(name="GND", net_class="ground", members=["U1.1"])],
    )
    report = validate_board(board)
    assert not any(i.code == "CIR030" for i in report.issues)


# ---------------------------------------------------------------------------
# CIR040 — length match groups
# ---------------------------------------------------------------------------


def test_cir040_fires_on_single_member_group() -> None:
    board = Board(
        name="lonely",
        components=[Component(refdes="U1", mpn="X")],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1"]),
            Net(name="CLK", net_class="signal",
                length_match_group="bus", members=["U1.2"]),
        ],
    )
    report = validate_board(board)
    assert any(i.code == "CIR040" for i in report.errors)


def test_cir040_silent_when_group_has_two_members() -> None:
    board = Board(
        name="paired",
        components=[Component(refdes="U1", mpn="X")],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1"]),
            Net(name="CLKA", net_class="signal",
                length_match_group="bus", members=["U1.2"]),
            Net(name="CLKB", net_class="signal",
                length_match_group="bus", members=["U1.3"]),
        ],
    )
    report = validate_board(board)
    assert not any(i.code == "CIR040" for i in report.issues)


def test_cir040_warns_when_constraint_missing_tolerance() -> None:
    board = Board(
        name="loose",
        components=[Component(refdes="U1", mpn="X")],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1"]),
            Net(name="A", net_class="signal", length_match_group="g", members=["U1.2"]),
            Net(name="B", net_class="signal", length_match_group="g", members=["U1.3"]),
        ],
        constraints=[Constraint(kind="length_match", targets=["g"])],  # no tolerance
    )
    report = validate_board(board)
    cir040_warnings = [i for i in report.warnings if i.code == "CIR040"]
    assert cir040_warnings, "expected a tolerance warning"


# ---------------------------------------------------------------------------
# CIR050 — partition isolation
# ---------------------------------------------------------------------------


def test_cir050_fires_when_net_crosses_partitions() -> None:
    board = Board(
        name="leaky",
        components=[
            Component(refdes="U1", mpn="X", partition="digital"),
            Component(refdes="U2", mpn="Y", partition="analog"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "U2.1"]),
            Net(name="DATA", net_class="signal", members=["U1.2", "U2.2"]),
        ],
    )
    report = validate_board(board)
    assert any(i.code == "CIR050" for i in report.errors)


def test_cir050_silent_with_explicit_cross_partition_ok() -> None:
    """A net flagged cross_partition_ok skips the check (reviewed crossing)."""
    board = Board(
        name="reviewed",
        components=[
            Component(refdes="U1", mpn="X", partition="digital"),
            Component(refdes="U2", mpn="Y", partition="analog"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "U2.1"]),
            Net(name="I2S", net_class="high_speed",
                cross_partition_ok=True, members=["U1.2", "U2.2"]),
        ],
    )
    report = validate_board(board)
    assert not any(i.code == "CIR050" for i in report.issues)


def test_cir050_silent_with_bridge_component() -> None:
    board = Board(
        name="bridged",
        components=[
            Component(refdes="U1", mpn="X", partition="digital"),
            Component(refdes="L1", mpn="FB", is_bridge=True),
            Component(refdes="U2", mpn="Y", partition="analog"),
        ],
        nets=[
            Net(name="GND", net_class="ground", members=["U1.1", "U2.1"]),
            Net(name="DATA", net_class="signal", members=["U1.2", "L1.1", "L1.2", "U2.2"]),
        ],
    )
    report = validate_board(board)
    # L1 is a bridge so the non_bridge_partitions set doesn't span analog+digital
    assert not any(i.code == "CIR050" for i in report.issues)


# ---------------------------------------------------------------------------
# 4-layer stackup smoke
# ---------------------------------------------------------------------------


def test_default_4layer_stackup_validates() -> None:
    board = Board(
        name="4l",
        stackup=Stackup.default_4layer_fr4(),
        fab=FabTarget(layer_count=4),
        components=[Component(refdes="U1", mpn="X")],
        nets=[Net(name="GND", net_class="ground", members=["U1.1"])],
    )
    report = validate_board(board)
    assert report.ok, [i.model_dump() for i in report.errors]


# ---------------------------------------------------------------------------
# Coverage meta — every M2 code has at least one fires + one silent test
# ---------------------------------------------------------------------------


def test_every_m2_code_has_both_cases() -> None:
    expected = {"CIR030", "CIR040", "CIR050"}
    import inspect

    from ki_mcp_pcb_core.cir import validation

    source = inspect.getsource(validation)
    found = {code for code in expected if f'code="{code}"' in source}
    assert found == expected


# ---------------------------------------------------------------------------
# The STM32 demo board itself
# ---------------------------------------------------------------------------


def test_stm32_audio_demo_validates_clean() -> None:
    """The M2 demo target validates with zero errors."""
    from ki_mcp_pcb_core.parsers.yaml import parse_yaml

    path = Path(__file__).resolve().parents[1] / "examples" / "stm32_audio.yaml"
    board = parse_yaml(path)
    report = validate_board(board)
    assert report.ok, "\n".join(
        f"  {i.code} {i.where or ''} {i.message}" for i in report.errors
    )

    # Specifically check that all M2 fields parsed correctly
    assert {c.partition for c in board.components if c.partition} == {
        "analog", "digital", "power"
    }
    assert any(n.length_match_group == "i2s" for n in board.nets)
    assert any(c.is_bridge for c in board.components)
    assert board.fab.layer_count == 4
    assert "In1.Cu" in board.stackup.power_plane_layers
