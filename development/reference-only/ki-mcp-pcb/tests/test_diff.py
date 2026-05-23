"""CIR diff tests."""

from __future__ import annotations

from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board, Component, Net
from ki_mcp_pcb_core.diff import diff_boards
from ki_mcp_pcb_core.parsers.yaml import parse_yaml

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_identical_boards_diff_to_empty() -> None:
    a = parse_yaml(EXAMPLES / "blinky.yaml")
    b = parse_yaml(EXAMPLES / "blinky.yaml")
    d = diff_boards(a, b)
    assert d.identical
    assert d.summary() == "identical"


def test_added_component_detected() -> None:
    a = parse_yaml(EXAMPLES / "blinky.yaml")
    b = parse_yaml(EXAMPLES / "blinky.yaml")
    b.components.append(Component(refdes="R1", mpn="GRM188R71C104KA01D"))
    d = diff_boards(a, b)
    assert not d.identical
    assert "R1" in d.components_added
    assert "+1 component" in d.summary()


def test_removed_component_detected() -> None:
    a = parse_yaml(EXAMPLES / "blinky.yaml")
    b = parse_yaml(EXAMPLES / "blinky.yaml")
    b.components.pop()
    d = diff_boards(a, b)
    assert d.components_removed


def test_changed_component_value_detected() -> None:
    a = parse_yaml(EXAMPLES / "blinky.yaml")
    b = parse_yaml(EXAMPLES / "blinky.yaml")
    target = next(c for c in b.components if c.refdes == "C1")
    target.value = "1uF"
    d = diff_boards(a, b)
    changes = [c for c in d.component_changes if c.refdes == "C1" and c.field == "value"]
    assert changes
    assert changes[0].right == "1uF"


def test_changed_net_members_detected() -> None:
    a = parse_yaml(EXAMPLES / "blinky.yaml")
    b = parse_yaml(EXAMPLES / "blinky.yaml")
    target = next(n for n in b.nets if n.name == "GND")
    target.members.append("U1.99")
    d = diff_boards(a, b)
    member_changes = [c for c in d.net_changes if c.field == "members"]
    assert member_changes


def test_net_class_change_detected() -> None:
    a = Board(name="t", components=[Component(refdes="U1", mpn="X")],
              nets=[Net(name="N1", net_class="signal", members=["U1.1"])])
    b = Board(name="t", components=[Component(refdes="U1", mpn="X")],
              nets=[Net(name="N1", net_class="high_speed", members=["U1.1"])])
    d = diff_boards(a, b)
    assert any(c.field == "net_class" for c in d.net_changes)


def test_name_change_detected() -> None:
    a = Board(name="alpha")
    b = Board(name="beta")
    d = diff_boards(a, b)
    assert d.name_changed == ("alpha", "beta")
    assert not d.identical


def test_diff_after_roundtrip_is_minimal(tmp_path: Path) -> None:
    """Write a board through KiCad and re-read it; semantic diff should be
    near-empty (lossy on placement / stackup overrides, but components +
    nets survive)."""
    import pytest
    pytest.importorskip("kiutils")

    from ki_mcp_pcb_core.backends.kicad import KiCadBackend

    board = parse_yaml(EXAMPLES / "blinky.yaml")
    backend = KiCadBackend()
    pro = backend.write_project(board, tmp_path)
    rebuilt = backend.read_project(pro)

    d = diff_boards(board, rebuilt)
    # Components added/removed must be empty
    assert not d.components_added
    assert not d.components_removed
    # Nets added/removed must be empty
    assert not d.nets_added
    assert not d.nets_removed
