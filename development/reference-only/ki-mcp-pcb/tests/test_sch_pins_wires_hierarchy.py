"""Schematic emission — pin-coordinate labels + hierarchical sheets.

The emitter writes the current KiCad-10 ``.kicad_sch`` format directly,
so these tests assert on the emitted file text rather than round-tripping
through kiutils (which only reads the obsolete KiCad-6 format).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from ki_mcp_pcb_core.cir.models import Board, Component, Net
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.synthesis.hierarchy import (
    needs_hierarchy,
    slice_board,
    write_hierarchical,
)
from ki_mcp_pcb_core.synthesis.schematic import write_schematic
from ki_mcp_pcb_core.synthesis.sym_lib import KICAD_SYMBOLS_ENV, SymbolLibIndex

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# ---------------------------------------------------------------------------
# Fixture symbol library — one symbol per MPN in the blinky demo
# ---------------------------------------------------------------------------


_FIXTURE = textwrap.dedent('''\
(kicad_symbol_lib (version 20211014) (generator kiutils)
  (symbol "ESP32-S3-WROOM-1"
    (in_bom yes) (on_board yes)
    (property "Reference" "U" (id 0) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "ESP32-S3" (id 1) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (symbol "ESP32-S3-WROOM-1_1_1"
      (pin power_in line (at -10 0 0) (length 2.54)
        (name "GND") (number "1"))
      (pin power_in line (at -10 5 0) (length 2.54)
        (name "VDD") (number "2"))
    )
  )
  (symbol "C"
    (in_bom yes) (on_board yes)
    (property "Reference" "C" (id 0) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "100nF" (id 1) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (symbol "C_1_1"
      (pin passive line (at 0 2.54 270) (length 1.27) (name "~") (number "1"))
      (pin passive line (at 0 -2.54 90) (length 1.27) (name "~") (number "2"))
    )
  )
)
''')


@pytest.fixture
def fake_lib(tmp_path, monkeypatch):
    """Project-local symbol library matching the blinky demo's MPNs."""
    libdir = tmp_path / "syms"
    libdir.mkdir()
    (libdir / "RF_Module.kicad_sym").write_text(_FIXTURE, encoding="utf-8")
    (libdir / "Device.kicad_sym").write_text(_FIXTURE, encoding="utf-8")
    monkeypatch.setenv(KICAD_SYMBOLS_ENV, str(libdir))
    return SymbolLibIndex(search_paths=[libdir])


# ---------------------------------------------------------------------------
# Pin-coordinate connectivity
# ---------------------------------------------------------------------------


def test_schematic_embeds_lib_symbols_when_lib_available(tmp_path, fake_lib) -> None:
    """Resolved symbols' definitions are embedded in ``lib_symbols``."""
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    out = write_schematic(board, tmp_path / "blinky.kicad_sch", symbol_index=fake_lib)

    text = out.read_text(encoding="utf-8")
    assert '(symbol "RF_Module:ESP32-S3-WROOM-1"' in text
    assert '(symbol "Device:C"' in text
    # Two pins on GND, two on 3V3 — each pin carries a label.
    assert text.count('(global_label "GND"') >= 2
    assert text.count('(global_label "3V3"') >= 2


def test_schematic_skips_components_when_no_lib(tmp_path) -> None:
    """With no reachable symbol library a component can't be embedded as a
    valid ``lib_symbols`` entry, so it is skipped — the file stays loadable."""
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    empty_index = SymbolLibIndex(search_paths=[])
    out = write_schematic(board, tmp_path / "blinky.kicad_sch", symbol_index=empty_index)

    text = out.read_text(encoding="utf-8")
    assert text.startswith("(kicad_sch")
    assert "(sheet_instances" in text  # a complete, structurally valid file
    assert '(lib_id "RF_Module:ESP32-S3-WROOM-1")' not in text


def test_schematic_labels_and_no_connects_cover_every_pin(tmp_path, fake_lib) -> None:
    """Every placed pin gets either a net label or a no-connect flag."""
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    out = write_schematic(board, tmp_path / "blinky.kicad_sch", symbol_index=fake_lib)

    text = out.read_text(encoding="utf-8")
    # Fixture: ESP32 has 2 pins, C has 2 pins — all four are on a net.
    assert text.count("(global_label ") == 4
    assert text.count("(no_connect") == 0


# ---------------------------------------------------------------------------
# Hierarchy decision
# ---------------------------------------------------------------------------


def test_needs_hierarchy_false_for_small_single_partition_board() -> None:
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    assert needs_hierarchy(board) is False


def test_needs_hierarchy_true_for_multiple_partitions() -> None:
    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    assert needs_hierarchy(board) is True


def test_needs_hierarchy_true_for_large_board() -> None:
    """Synthetic board with 35 components triggers hierarchy."""
    comps = [Component(refdes=f"R{i}", mpn="X") for i in range(35)]
    nets = [Net(name="GND", net_class="ground", members=[f"R{i}.1" for i in range(35)])]
    board = Board(name="big", components=comps, nets=nets)
    assert needs_hierarchy(board) is True


# ---------------------------------------------------------------------------
# Slicing
# ---------------------------------------------------------------------------


def test_slice_board_groups_by_cluster() -> None:
    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    slices = slice_board(board)
    placed = {c.refdes for sl in slices for c in sl.components}
    assert placed == {c.refdes for c in board.components}


def test_slice_filenames_unique_and_well_formed() -> None:
    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    slices = slice_board(board)
    fnames = [sl.file_name for sl in slices]
    assert len(fnames) == len(set(fnames))
    for fn in fnames:
        assert fn.startswith(board.name + "__")
        assert fn.endswith(".kicad_sch")


# ---------------------------------------------------------------------------
# Hierarchical emission
# ---------------------------------------------------------------------------


def _count_sheet_blocks(text: str) -> int:
    """Count hierarchical ``(sheet …)`` blocks (one-tab indent)."""
    return text.count("\n\t(sheet\n")


def test_write_hierarchical_emits_parent_plus_children(tmp_path) -> None:
    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    parent_path = tmp_path / "out.kicad_sch"
    written = write_hierarchical(board, parent_path)

    assert written[0] == parent_path
    assert len(written) >= 2
    for path in written:
        assert path.exists()
        assert path.read_text(encoding="utf-8").startswith("(kicad_sch")


def test_hierarchical_parent_contains_one_sheet_per_slice(tmp_path) -> None:
    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    parent_path = tmp_path / "out.kicad_sch"
    write_hierarchical(board, parent_path)

    parent_text = parent_path.read_text(encoding="utf-8")
    assert _count_sheet_blocks(parent_text) == len(slice_board(board))


def test_kicadbackend_uses_hierarchy_for_multi_partition_board(tmp_path) -> None:
    """KiCadBackend routes a multi-partition board through hierarchical emission."""
    from ki_mcp_pcb_core.backends.kicad import KiCadBackend

    board = parse_yaml(EXAMPLES / "stm32_audio.yaml")
    KiCadBackend().write_project(board, tmp_path)

    parent_text = (tmp_path / f"{board.name}.kicad_sch").read_text(encoding="utf-8")
    assert _count_sheet_blocks(parent_text) >= 2


def test_kicadbackend_stays_flat_for_small_single_partition_board(tmp_path) -> None:
    from ki_mcp_pcb_core.backends.kicad import KiCadBackend

    board = parse_yaml(EXAMPLES / "blinky.yaml")
    KiCadBackend().write_project(board, tmp_path)

    text = (tmp_path / f"{board.name}.kicad_sch").read_text(encoding="utf-8")
    # A flat schematic — no child sheets, but the actual component symbols.
    assert _count_sheet_blocks(text) == 0
    for comp in board.components:
        assert f'(property "Reference" "{comp.refdes}"' in text
