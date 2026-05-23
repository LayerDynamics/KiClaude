"""Sourcing reporter tests."""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board, Component, Net
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.sourcing import check_sourcing

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_demo_board_resolves_all_components() -> None:
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    report = check_sourcing(board)
    assert report.ok
    statuses = {e.status for e in report.entries}
    assert "missing" not in statuses


def test_jlc_mappings_surface_lcsc() -> None:
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    report = check_sourcing(board)
    by_mpn = {e.mpn: e for e in report.entries}
    # 100nF cap has a documented LCSC in libs/footprints.yaml
    assert by_mpn["GRM188R71C104KA01D"].status == "in_stock_jlc"
    assert by_mpn["GRM188R71C104KA01D"].lcsc == "C14663"


def test_unknown_mpn_reports_missing() -> None:
    board = Board(
        name="t",
        components=[Component(refdes="U99", mpn="UNKNOWN-PART-XYZ")],
        nets=[Net(name="GND", net_class="ground", members=["U99.1"])],
    )
    report = check_sourcing(board, registry_path=Path("/dev/null"))
    assert not report.ok
    assert report.missing[0].mpn == "UNKNOWN-PART-XYZ"
