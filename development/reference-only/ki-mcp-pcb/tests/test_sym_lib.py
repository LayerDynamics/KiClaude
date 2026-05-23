"""Symbol library lookup tests — pin coordinate parsing + discovery."""

from __future__ import annotations

import textwrap

import pytest
from ki_mcp_pcb_core.synthesis.sym_lib import (
    KICAD_SYMBOLS_ENV,
    SymbolLibIndex,
    find_symbol_lib_paths,
)

# ---------------------------------------------------------------------------
# Fixture symbol library
# ---------------------------------------------------------------------------


_FIXTURE_LIB = textwrap.dedent('''\
(kicad_symbol_lib (version 20211014) (generator kiutils)
  (symbol "MyChip"
    (pin_numbers hide) (pin_names (offset 1.016) hide) (in_bom yes) (on_board yes)
    (property "Reference" "U" (id 0) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "MyChip" (id 1) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (symbol "MyChip_1_1"
      (pin power_in line (at -5 0 0) (length 2.54)
        (name "VDD" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin power_in line (at -5 -2.54 0) (length 2.54)
        (name "GND" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
      (pin input line (at 5 0 180) (length 2.54)
        (name "OUT" (effects (font (size 1.27 1.27))))
        (number "3" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
''')


@pytest.fixture
def lib_dir(tmp_path, monkeypatch):
    """Project-local library dir with one fake symbol file."""
    lib_path = tmp_path / "FakeLib.kicad_sym"
    lib_path.write_text(_FIXTURE_LIB, encoding="utf-8")
    monkeypatch.setenv(KICAD_SYMBOLS_ENV, str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_find_symbol_lib_paths_respects_env_override(lib_dir) -> None:
    paths = find_symbol_lib_paths()
    assert lib_dir in paths


def test_find_symbol_lib_paths_returns_only_existing_dirs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(KICAD_SYMBOLS_ENV, str(tmp_path / "does-not-exist"))
    paths = find_symbol_lib_paths()
    assert all(p.is_dir() for p in paths)


# ---------------------------------------------------------------------------
# Pin lookup
# ---------------------------------------------------------------------------


def test_pin_positions_known_symbol(lib_dir) -> None:
    idx = SymbolLibIndex(search_paths=[lib_dir])
    pins = idx.pin_positions("FakeLib:MyChip")
    assert pins is not None
    # Matches the fixture: VDD at (-5,0), GND at (-5,-2.54), OUT at (5,0).
    assert pins["1"] == (-5.0, 0.0)
    assert pins["2"] == (-5.0, -2.54)
    assert pins["3"] == (5.0, 0.0)


def test_pin_positions_unknown_library_returns_none(lib_dir) -> None:
    idx = SymbolLibIndex(search_paths=[lib_dir])
    assert idx.pin_positions("Missing:Whatever") is None


def test_pin_positions_unknown_symbol_returns_none(lib_dir) -> None:
    idx = SymbolLibIndex(search_paths=[lib_dir])
    assert idx.pin_positions("FakeLib:NoSuchSymbol") is None


def test_pin_positions_caches_per_lib_id(lib_dir) -> None:
    """Repeated lookups for the same lib_id should hit the cache."""
    idx = SymbolLibIndex(search_paths=[lib_dir])
    first = idx.pin_positions("FakeLib:MyChip")
    second = idx.pin_positions("FakeLib:MyChip")
    assert first is second  # identical object → cached


def test_pin_positions_malformed_lib_id_returns_none(lib_dir) -> None:
    idx = SymbolLibIndex(search_paths=[lib_dir])
    assert idx.pin_positions("not-a-valid-lib-id") is None
    assert idx.pin_positions("") is None


def test_index_with_empty_search_paths_returns_none() -> None:
    """No search paths means no symbol ever resolves — graceful degradation."""
    idx = SymbolLibIndex(search_paths=[])
    assert idx.pin_positions("Device:R") is None
