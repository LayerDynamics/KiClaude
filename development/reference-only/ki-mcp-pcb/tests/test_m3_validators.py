"""Negative + positive cases for M3 design-intent validators."""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core.cir.models import (
    Board,
    Component,
    FabTarget,
    Net,
    Stackup,
)
from ki_mcp_pcb_core.cir.validation import validate_board


def _board_with(nets: list[Net], components: list[Component] | None = None, *,
                stackup: Stackup | None = None, fab: FabTarget | None = None) -> Board:
    return Board(
        name="t",
        stackup=stackup or Stackup.default_4layer_fr4(),
        fab=fab or FabTarget(layer_count=4),
        components=components or [Component(refdes="U1", mpn="X")],
        nets=nets,
    )


# ---------------------------------------------------------------------------
# CIR060 — diff pair declarations
# ---------------------------------------------------------------------------


def test_cir060_fires_when_partner_missing() -> None:
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="USB_DP", net_class="differential", diff_pair_with="USB_DM",
            members=["U1.2"]),
    ])
    report = validate_board(board)
    assert any(i.code == "CIR060" for i in report.errors)


def test_cir060_fires_when_partner_not_bidirectional() -> None:
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="A", net_class="differential", diff_pair_with="B",
            length_match_group="g", members=["U1.2"]),
        Net(name="B", net_class="differential", diff_pair_with="C",  # wrong!
            length_match_group="g", members=["U1.3"]),
        Net(name="C", net_class="differential", diff_pair_with="B",
            length_match_group="g", members=["U1.4"]),
    ])
    report = validate_board(board)
    assert any(i.code == "CIR060" for i in report.errors)


def test_cir060_warns_when_no_length_match_group() -> None:
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="USB_DP", net_class="differential", diff_pair_with="USB_DM",
            members=["U1.2"]),
        Net(name="USB_DM", net_class="differential", diff_pair_with="USB_DP",
            members=["U1.3"]),
    ])
    report = validate_board(board)
    cir060_warnings = [i for i in report.warnings if i.code == "CIR060"]
    assert cir060_warnings


def test_cir060_silent_when_diff_pair_well_formed() -> None:
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="USB_DP", net_class="differential", diff_pair_with="USB_DM",
            length_match_group="usb_hs", members=["U1.2"]),
        Net(name="USB_DM", net_class="differential", diff_pair_with="USB_DP",
            length_match_group="usb_hs", members=["U1.3"]),
    ])
    report = validate_board(board)
    assert not any(i.code == "CIR060" for i in report.issues)


# ---------------------------------------------------------------------------
# CIR070 — controlled impedance
# ---------------------------------------------------------------------------


def test_cir070_fires_when_target_unreachable() -> None:
    """A very low impedance target with default trace geometry is unreachable."""
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="LV", net_class="signal", target_impedance_ohm=10.0,
            reference_plane="In1.Cu", members=["U1.2"]),
    ])
    report = validate_board(board)
    assert any(i.code == "CIR070" and i.severity == "error" for i in report.issues)


def test_cir070_silent_when_geometry_matches_target() -> None:
    """Demo board's USB+Eth diff pairs hit their targets cleanly."""
    from ki_mcp_pcb_core.parsers.yaml import parse_yaml

    path = Path(__file__).resolve().parents[1] / "examples" / "usb_eth_phy.yaml"
    board = parse_yaml(path)
    report = validate_board(board)
    cir070 = [i for i in report.issues if i.code == "CIR070" and i.severity == "error"]
    assert not cir070, [i.message for i in cir070]


# ---------------------------------------------------------------------------
# CIR090 — return path
# ---------------------------------------------------------------------------


def test_cir090_warns_when_hs_net_has_no_reference_plane() -> None:
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="CLK", net_class="high_speed", members=["U1.2"]),  # no plane
    ])
    report = validate_board(board)
    assert any(i.code == "CIR090" and i.severity == "warning" for i in report.warnings)


def test_cir090_fires_when_reference_plane_doesnt_exist() -> None:
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="CLK", net_class="high_speed",
            reference_plane="Nonexistent.Cu", members=["U1.2"]),
    ])
    report = validate_board(board)
    assert any(i.code == "CIR090" and i.severity == "error" for i in report.errors)


def test_cir090_silent_when_reference_plane_valid() -> None:
    board = _board_with([
        Net(name="GND", net_class="ground", members=["U1.1"]),
        Net(name="CLK", net_class="high_speed",
            reference_plane="In1.Cu", members=["U1.2"]),
    ])
    report = validate_board(board)
    assert not any(i.code == "CIR090" for i in report.issues)


# ---------------------------------------------------------------------------
# USB+Eth demo board
# ---------------------------------------------------------------------------


def test_usb_eth_phy_demo_validates_clean() -> None:
    from ki_mcp_pcb_core.parsers.yaml import parse_yaml

    path = Path(__file__).resolve().parents[1] / "examples" / "usb_eth_phy.yaml"
    board = parse_yaml(path)
    report = validate_board(board)
    assert report.ok, "\n".join(
        f"  {i.code} {i.where or ''} {i.message}" for i in report.errors
    )

    # Specifically check M3 fields parsed
    diff_pair_count = sum(1 for n in board.nets if n.diff_pair_with)
    assert diff_pair_count == 6  # USB± + ETH_TX± + ETH_RX±


# ---------------------------------------------------------------------------
# Coverage meta
# ---------------------------------------------------------------------------


def test_every_m3_code_present() -> None:
    expected = {"CIR060", "CIR070", "CIR090"}  # CIR080 is post-route only
    import inspect

    from ki_mcp_pcb_core.cir import validation

    source = inspect.getsource(validation)
    found = {code for code in expected if f'code="{code}"' in source}
    assert found == expected
