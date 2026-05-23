"""Schematic synthesis (M2) — KiCad-10 ``.kicad_sch`` emission + ERC.

The emitter writes the current KiCad schematic format directly as
S-expressions (kiutils only writes the obsolete KiCad-6 format, which
KiCad 9/10 can't load), so these tests assert on the emitted file text
and — when KiCad is installed — on a real ``kicad-cli sch erc`` run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.synthesis.resolver import UnresolvedMPNError
from ki_mcp_pcb_core.synthesis.schematic import write_schematic

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_schematic_is_kicad10_format(tmp_path: Path) -> None:
    """The file announces the current KiCad schematic format version."""
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    out = write_schematic(board, tmp_path / "blinky.kicad_sch")

    text = out.read_text(encoding="utf-8")
    assert text.startswith("(kicad_sch")
    assert "(version 20250114)" in text
    assert "(lib_symbols" in text


def test_schematic_places_every_component(tmp_path: Path) -> None:
    """Each CIR component lands as a placed symbol carrying its refdes + MPN."""
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    out = write_schematic(board, tmp_path / "blinky.kicad_sch")

    text = out.read_text(encoding="utf-8")
    for comp in board.components:
        assert f'(property "Reference" "{comp.refdes}"' in text
        assert f'(property "MPN" "{comp.mpn}"' in text


def test_schematic_labels_every_net_pin(tmp_path: Path) -> None:
    """Every (refdes, pin) on a net gets a label with the net name.

    Two pins sharing a net carry an identically-named label and are
    therefore connected; each power/ground net also gets a PWR_FLAG
    label, so the count is at least one per pin member.
    """
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    out = write_schematic(board, tmp_path / "blinky.kicad_sch")

    text = out.read_text(encoding="utf-8")
    for net in board.nets:
        labels = text.count(f'(global_label "{net.name}"')
        assert labels >= len(net.members), net.name


def test_schematic_fails_closed_on_unresolved_mpn(tmp_path: Path) -> None:
    """Same rule as the PCB backend: a missing MPN aborts synthesis."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        """
cir_version: "0.2"
name: bad
components:
  - refdes: U1
    mpn: TOTALLY-MADE-UP-PART
nets:
  - name: GND
    net_class: ground
    members: ["U1.1"]
""".strip(),
        encoding="utf-8",
    )
    board = parse_yaml(bad_yaml)
    with pytest.raises(UnresolvedMPNError):
        write_schematic(board, tmp_path / "out.kicad_sch")


def test_kicad_backend_emits_schematic(tmp_path: Path) -> None:
    """KiCadBackend.write_project produces the ``.kicad_sch`` alongside the rest."""
    from ki_mcp_pcb_core.backends.kicad import KiCadBackend

    board = parse_yaml(EXAMPLES / "blinky.yaml")
    KiCadBackend().write_project(board, tmp_path)
    assert (tmp_path / f"{board.name}.kicad_sch").exists()


def test_schematic_passes_erc(tmp_path: Path) -> None:
    """The synthesized schematic loads and runs ERC-clean in real KiCad.

    Skipped where kicad-cli isn't installed; CI's ``kicad-build`` job runs
    it for real.
    """
    from ki_mcp_pcb_core import _kicad_cli

    if not _kicad_cli.is_available():
        pytest.skip("kicad-cli not available — schematic ERC needs real KiCad")

    from ki_mcp_pcb_core.validation.erc import run_erc

    board = parse_yaml(EXAMPLES / "blinky.yaml")
    out = write_schematic(board, tmp_path / "blinky.kicad_sch")
    result = run_erc(out)

    assert result.ok, [i.description for i in result.issues]
    assert result.errors == 0
