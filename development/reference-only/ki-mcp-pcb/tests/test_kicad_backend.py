"""KiCadBackend write_project tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.backends.kicad import KiCadBackend
from ki_mcp_pcb_core.parsers.yaml import parse_yaml

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def blinky():
    return parse_yaml(EXAMPLES / "blinky.yaml")


def test_write_project_emits_three_files(tmp_path: Path, blinky) -> None:
    pro_path = KiCadBackend().write_project(blinky, tmp_path)
    assert pro_path.exists()
    assert pro_path.suffix == ".kicad_pro"
    assert (tmp_path / f"{blinky.name}.net").exists()
    assert (tmp_path / f"{blinky.name}.kicad_pcb").exists()


def test_kicad_pro_is_valid_json_with_design_rules(tmp_path: Path, blinky) -> None:
    import json
    pro_path = KiCadBackend().write_project(blinky, tmp_path)
    data = json.loads(pro_path.read_text(encoding="utf-8"))
    assert data["meta"]["filename"].endswith(".kicad_pro")
    rules = data["board"]["design_settings"]["rules"]
    assert rules["min_track_width"] == blinky.fab.min_trace_mm
    assert rules["min_clearance"] == blinky.fab.min_space_mm


def test_kicad_pro_carries_min_through_hole_diameter(tmp_path: Path, blinky) -> None:
    """The PTH min-hole rule tracks the fab profile.

    KiCad's 0.3 mm default would otherwise reject the 0.2 mm holes used by
    common module/IC footprints when the populate step is skipped.
    """
    import json
    pro_path = KiCadBackend().write_project(blinky, tmp_path)
    rules = json.loads(
        pro_path.read_text(encoding="utf-8")
    )["board"]["design_settings"]["rules"]
    assert rules["min_through_hole_diameter"] == blinky.fab.min_drill_mm


def test_netlist_contains_every_component_and_net(tmp_path: Path, blinky) -> None:
    KiCadBackend().write_project(blinky, tmp_path)
    netlist = (tmp_path / f"{blinky.name}.net").read_text(encoding="utf-8")
    for comp in blinky.components:
        assert f'(ref "{comp.refdes}")' in netlist
    for net in blinky.nets:
        assert f'(name "{net.name}")' in netlist


def test_pcb_skeleton_roundtrips_via_kiutils(tmp_path: Path, blinky) -> None:
    """If we write a board, kiutils must be able to re-read it without error."""
    pytest.importorskip("kiutils")
    from kiutils.board import Board as KBoard

    KiCadBackend().write_project(blinky, tmp_path)
    pcb_path = tmp_path / f"{blinky.name}.kicad_pcb"
    # Should not raise.
    kb = KBoard.from_file(str(pcb_path))
    assert kb.general.thickness == blinky.stackup.finished_thickness_mm


def test_unresolved_mpn_fails_synthesis(tmp_path: Path) -> None:
    """Synthesis must fail closed when an MPN isn't in the registry."""
    bad_yaml = (tmp_path / "bad.yaml")
    bad_yaml.write_text(
        """
cir_version: "0.1"
name: bad
components:
  - refdes: U1
    mpn: PART-THAT-DOES-NOT-EXIST
nets:
  - name: GND
    net_class: ground
    members: ["U1.1"]
""".strip(),
        encoding="utf-8",
    )
    board = parse_yaml(bad_yaml)
    from ki_mcp_pcb_core.synthesis.resolver import UnresolvedMPNError
    with pytest.raises(UnresolvedMPNError):
        KiCadBackend().write_project(board, tmp_path)


def test_netlist_uses_resolved_footprint_from_registry(tmp_path: Path, blinky) -> None:
    KiCadBackend().write_project(blinky, tmp_path)
    netlist = (tmp_path / f"{blinky.name}.net").read_text(encoding="utf-8")
    # ESP32-S3-WROOM-1 in libs/footprints.yaml resolves to an RF_Module footprint
    assert "RF_Module:" in netlist
    # 100nF cap resolves to Capacitor_SMD:C_0603_1608Metric and has an LCSC
    assert "Capacitor_SMD:C_0603_1608Metric" in netlist
    assert "C14663" in netlist  # LCSC for the 100nF cap
