"""CIR → KiCad → CIR round-trip (electrical model only).

The round-trip is intentionally lossy on geometry (placement, stackup
overrides). What it preserves: every component refdes + MPN, every net
name + membership, the broad net classification.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.backends.kicad import KiCadBackend
from ki_mcp_pcb_core.parsers.yaml import parse_yaml

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _examples() -> list[Path]:
    return sorted(EXAMPLES.glob("*.yaml"))


@pytest.mark.parametrize("path", _examples())
def test_kicad_roundtrip_preserves_components(tmp_path: Path, path: Path) -> None:
    pytest.importorskip("kiutils")

    original = parse_yaml(path)
    backend = KiCadBackend()
    pro_path = backend.write_project(original, tmp_path)
    rebuilt = backend.read_project(pro_path)

    # Every refdes survives
    assert {c.refdes for c in rebuilt.components} == {c.refdes for c in original.components}

    # Every MPN survives
    by_ref_orig = {c.refdes: c.mpn for c in original.components}
    by_ref_back = {c.refdes: c.mpn for c in rebuilt.components}
    assert by_ref_back == by_ref_orig


@pytest.mark.parametrize("path", _examples())
def test_kicad_roundtrip_preserves_nets(tmp_path: Path, path: Path) -> None:
    pytest.importorskip("kiutils")

    original = parse_yaml(path)
    backend = KiCadBackend()
    pro_path = backend.write_project(original, tmp_path)
    rebuilt = backend.read_project(pro_path)

    assert {n.name for n in rebuilt.nets} == {n.name for n in original.nets}

    # Net membership: every (refdes, pin) survives
    def members_set(board) -> set[tuple[str, str]]:
        return {(n.name, m) for n in board.nets for m in n.members}

    assert members_set(rebuilt) == members_set(original)


def test_kicad_roundtrip_recovers_ground_class(tmp_path: Path) -> None:
    """The class guesser maps GND → ground."""
    pytest.importorskip("kiutils")

    board = parse_yaml(EXAMPLES / "blinky.yaml")
    backend = KiCadBackend()
    pro_path = backend.write_project(board, tmp_path)
    rebuilt = backend.read_project(pro_path)

    gnd = next(n for n in rebuilt.nets if n.name == "GND")
    assert gnd.net_class == "ground"


def test_kicad_roundtrip_recovers_power_class(tmp_path: Path) -> None:
    pytest.importorskip("kiutils")

    board = parse_yaml(EXAMPLES / "blinky.yaml")
    backend = KiCadBackend()
    pro_path = backend.write_project(board, tmp_path)
    rebuilt = backend.read_project(pro_path)

    vcc = next(n for n in rebuilt.nets if n.name == "3V3")
    assert vcc.net_class == "power"


def test_read_project_errors_without_netlist(tmp_path: Path) -> None:
    """No .net file alongside the .kicad_pro → clear FileNotFoundError."""
    pro = tmp_path / "ghost.kicad_pro"
    pro.write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        KiCadBackend().read_project(pro)
