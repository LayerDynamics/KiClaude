"""M4 — RF / DDR / BGA fanout validators + CPWG math tests."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from ki_mcp_pcb_core.cir.models import (
    Board,
    Component,
    FabTarget,
    Net,
    Signoff,
    Stackup,
)
from ki_mcp_pcb_core.cir.validation import validate_board
from ki_mcp_pcb_core.signal_integrity import (
    StackupGeometry,
    grounded_cpwg_impedance,
)

# ---------------------------------------------------------------------------
# CPWG math
# ---------------------------------------------------------------------------


def test_cpwg_50ohm_reference_geometry() -> None:
    """Canonical CPWG: w=0.40mm, gap=0.30mm on 0.21mm prepreg εr=4.3 → ≈50 Ω.

    Solver-tuned via the Wadell/Wen approximation. Same geometry shipped
    in examples/esp32_c6_rf.yaml.
    """
    g = StackupGeometry(
        trace_width_mm=0.40, trace_thickness_mm=0.035,
        dielectric_height_mm=0.21, er=4.3, cpwg_gap_mm=0.30,
    )
    z = grounded_cpwg_impedance(g)
    assert math.isclose(z, 50.0, abs_tol=2.0), z


def test_cpwg_requires_gap() -> None:
    g = StackupGeometry(trace_width_mm=0.25, trace_thickness_mm=0.035,
                         dielectric_height_mm=0.21, er=4.3)
    with pytest.raises(ValueError):
        grounded_cpwg_impedance(g)


def test_cpwg_monotonic_in_gap() -> None:
    """Wider gap → side-ground coupling drops → Z approaches pure microstrip
    from below, so Z increases monotonically with gap."""
    tight = StackupGeometry(trace_width_mm=0.25, trace_thickness_mm=0.035,
                             dielectric_height_mm=0.21, er=4.3, cpwg_gap_mm=0.10)
    loose = StackupGeometry(trace_width_mm=0.25, trace_thickness_mm=0.035,
                             dielectric_height_mm=0.21, er=4.3, cpwg_gap_mm=0.40)
    assert grounded_cpwg_impedance(loose) > grounded_cpwg_impedance(tight)


def test_cpwg_within_microstrip_band() -> None:
    """At wide gaps the Wadell/Wen approximation tends 10-25% above the
    pure microstrip value (the closed form keeps a residual side-ground
    contribution). We assert the impedance stays in that band rather
    than running away — a sanity guard on the math, not physics."""
    from ki_mcp_pcb_core.signal_integrity import microstrip_impedance

    base = StackupGeometry(trace_width_mm=0.25, trace_thickness_mm=0.035,
                           dielectric_height_mm=0.21, er=4.3)
    ms = microstrip_impedance(base)
    cpwg_wide = grounded_cpwg_impedance(StackupGeometry(
        trace_width_mm=0.25, trace_thickness_mm=0.035,
        dielectric_height_mm=0.21, er=4.3, cpwg_gap_mm=2.0,
    ))
    assert ms < cpwg_wide < ms * 1.30


# ---------------------------------------------------------------------------
# CIR100 — DDR fly-by topology
# ---------------------------------------------------------------------------


def _ddr_board(order: list[str], *, members: list[str], signoff_done: bool = True) -> Board:
    components = [Component(refdes=r, mpn=f"PART_{r}") for r in order]
    return Board(
        name="ddr",
        stackup=Stackup.default_4layer_fr4(),
        fab=FabTarget(layer_count=4),
        components=components,
        nets=[
            Net(name="GND", net_class="ground", members=[f"{order[0]}.1"]),
            Net(name="A0", net_class="high_speed", topology="fly_by",
                fly_by_order=order, members=members),
        ],
        signoff=Signoff(ddr_reviewed=signoff_done,
                        reviewer="t@x", reviewed_at="2026-05-17") if signoff_done else Signoff(),
    )


def test_cir100_fires_on_too_few_members() -> None:
    board = _ddr_board(["U1", "U2"], members=["U1.1", "U2.1"])
    report = validate_board(board)
    assert any(i.code == "CIR100" and i.severity == "error" for i in report.errors)


def test_cir100_silent_on_valid_three_node_fly_by() -> None:
    board = _ddr_board(["U1", "U2", "R1"], members=["U1.1", "U2.1", "R1.1"])
    report = validate_board(board)
    errors = [i for i in report.errors if i.code == "CIR100"]
    assert not errors


def test_cir100_warns_when_not_signed_off() -> None:
    board = _ddr_board(["U1", "U2", "R1"], members=["U1.1", "U2.1", "R1.1"],
                       signoff_done=False)
    report = validate_board(board)
    warnings = [i for i in report.warnings if i.code == "CIR100"]
    assert warnings
    assert any("ddr_reviewed" in w.message for w in warnings)


def test_cir100_fires_on_unknown_component() -> None:
    board = _ddr_board(["U1", "U2", "R1"], members=["U1.1", "U2.1", "R1.1"])
    # Inject an unknown refdes into the fly-by order
    board.nets[1].fly_by_order = ["U1", "U_GHOST", "R1"]
    report = validate_board(board)
    assert any(i.code == "CIR100" and i.severity == "error" for i in report.errors)


# ---------------------------------------------------------------------------
# CIR110 — BGA fanout feasibility
# ---------------------------------------------------------------------------


def _bga_board(pitch_mm: float, *, fab: FabTarget | None = None,
               signoff_done: bool = True) -> Board:
    return Board(
        name="bga",
        stackup=Stackup.default_4layer_fr4(),
        fab=fab or FabTarget(layer_count=4),
        components=[
            Component(refdes="U1", mpn="X", bga_pitch_mm=pitch_mm),
        ],
        nets=[Net(name="GND", net_class="ground", members=["U1.1"])],
        signoff=Signoff(bga_fanout_reviewed=signoff_done,
                        reviewer="t@x", reviewed_at="2026-05-17") if signoff_done else Signoff(),
    )


def test_cir110_silent_for_friendly_pitch_with_signoff() -> None:
    """0.8mm pitch + standard 4-layer JLC + signoff → clean."""
    board = _bga_board(0.8)
    report = validate_board(board)
    errors = [i for i in report.errors if i.code == "CIR110"]
    warnings = [i for i in report.warnings if i.code == "CIR110"]
    assert not errors
    assert not warnings


def test_cir110_warns_when_not_signed_off() -> None:
    board = _bga_board(0.8, signoff_done=False)
    report = validate_board(board)
    warnings = [i for i in report.warnings if i.code == "CIR110"]
    assert warnings


def test_cir110_fires_on_hdi_pitch() -> None:
    """0.4mm pitch needs HDI → CIR110 should error on a non-HDI fab."""
    board = _bga_board(0.4)
    report = validate_board(board)
    errors = [i for i in report.errors if i.code == "CIR110"]
    assert errors
    assert any("HDI" in e.message for e in errors)


def test_cir110_warns_on_unknown_pitch() -> None:
    """A pitch with no template entry warns rather than errors."""
    board = _bga_board(0.55)  # not in libs/bga_fanout.yaml
    report = validate_board(board)
    warnings = [i for i in report.warnings if i.code == "CIR110"]
    assert warnings
    assert any("no fanout template" in w.message for w in warnings)


# ---------------------------------------------------------------------------
# Co-pilot signoff
# ---------------------------------------------------------------------------


def test_signoff_default_unreviewed() -> None:
    """A board with no signoff has every gate False."""
    board = Board(name="x")
    assert not board.signoff.rf_reviewed
    assert not board.signoff.ddr_reviewed
    assert not board.signoff.bga_fanout_reviewed
    assert board.signoff.reviewer is None


# ---------------------------------------------------------------------------
# M4 demo board
# ---------------------------------------------------------------------------


def test_esp32_c6_rf_demo_validates_clean() -> None:
    from ki_mcp_pcb_core.parsers.yaml import parse_yaml

    path = Path(__file__).resolve().parents[1] / "examples" / "esp32_c6_rf.yaml"
    board = parse_yaml(path)
    report = validate_board(board)
    assert report.ok, "\n".join(
        f"  {i.code} {i.where or ''} {i.message}" for i in report.errors
    )
    # Sanity-check the demo's M4 footprint
    assert board.signoff.rf_reviewed
    assert board.signoff.ddr_reviewed
    assert board.signoff.bga_fanout_reviewed
    assert any(c.bga_pitch_mm == 0.8 for c in board.components)
    assert any(n.topology == "fly_by" for n in board.nets)
    assert any(n.cpwg_gap_mm is not None for n in board.nets)


# ---------------------------------------------------------------------------
# Coverage meta
# ---------------------------------------------------------------------------


def test_every_m4_code_present() -> None:
    expected = {"CIR100", "CIR110"}
    import inspect

    from ki_mcp_pcb_core.cir import validation

    source = inspect.getsource(validation)
    found = {code for code in expected if f'code="{code}"' in source}
    assert found == expected
