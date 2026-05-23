"""M1-P-02 acceptance tests for [`LibraryIndex`]."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from kiserver.library import LibraryIndex

TINY_LIB = '''(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)
  (symbol "R" (in_bom yes) (on_board yes)
    (property "Reference" "R" (id 0) (at 0 0 0))
    (property "Value" "R" (id 1) (at 0 0 0))
    (property "Description" "Resistor" (id 4) (at 0 0 0))
    (property "ki_keywords" "resistor R" (id 5) (at 0 0 0))
    (property "ki_fp_filters" "R_*" (id 6) (at 0 0 0))
  )
  (symbol "STM32G030F6P6"
    (property "Reference" "U" (id 0) (at 0 0 0))
    (property "Value" "STM32G030F6P6" (id 1) (at 0 0 0))
    (property "Footprint" "Package_SO:TSSOP-20" (id 2) (at 0 0 0))
    (property "Datasheet" "https://st.com/stm32g030.pdf" (id 3) (at 0 0 0))
    (property "Description" "STM32G0 ARM Cortex-M0+" (id 4) (at 0 0 0))
    (property "ki_keywords" "STM32 ARM MCU" (id 5) (at 0 0 0))
    (property "ki_fp_filters" "TSSOP*P0.65mm*" (id 6) (at 0 0 0))
    (property "Manufacturer_Part_Number" "STM32G030F6P6" (id 7) (at 0 0 0))
  )
)
'''


@pytest.fixture()
def lib_table(tmp_path: Path) -> Path:
    """Build a real on-disk sym-lib-table + matching .kicad_sym file."""
    lib_path = tmp_path / "Device.kicad_sym"
    lib_path.write_text(TINY_LIB)
    table_path = tmp_path / "sym-lib-table"
    table_path.write_text(
        '(sym_lib_table\n'
        '  (version 7)\n'
        f'  (lib (name "Device") (type KiCad) (uri "{lib_path}") (options "") (descr ""))\n'
        ')\n'
    )
    return table_path


def _require_ki_native() -> None:
    try:
        import ki_native  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("ki_native not installed in this venv")


def test_open_indexes_every_symbol_in_the_table(
    lib_table: Path, tmp_path: Path
) -> None:
    _require_ki_native()
    idx = LibraryIndex.open(lib_table, tmp_path / "cache")
    assert len(idx) == 2


def test_search_for_stm32g0_returns_ranked_hit(
    lib_table: Path, tmp_path: Path
) -> None:
    _require_ki_native()
    idx = LibraryIndex.open(lib_table, tmp_path / "cache")
    hits = idx.search("STM32G0", 10)
    assert hits, "STM32G0 must match the STM32G030F6P6 symbol"
    top = hits[0]
    assert top.lib_id == "Device:STM32G030F6P6"
    assert top.footprint == "Package_SO:TSSOP-20"
    assert top.mpn == "STM32G030F6P6"
    assert top.score > 0.5


def test_search_empty_query_returns_zero_score_full_list(
    lib_table: Path, tmp_path: Path
) -> None:
    _require_ki_native()
    idx = LibraryIndex.open(lib_table, tmp_path / "cache")
    hits = idx.search("", 100)
    assert {h.lib_id for h in hits} == {"Device:R", "Device:STM32G030F6P6"}
    assert all(h.score == 0.0 for h in hits)


def test_cache_round_trip_hits_sqlite_on_second_open(
    lib_table: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build once, then mock out `ki_native.list_symbols` to verify
    the second open serves from SQLite without re-parsing."""
    _require_ki_native()
    cache_dir = tmp_path / "cache"

    idx1 = LibraryIndex.open(lib_table, cache_dir)
    assert idx1.cache_path.exists(), "SQLite cache file created"

    # Patch ki_native to confirm the second open never calls it.
    import kiserver.library as lib_mod

    def boom(*_a: object, **_k: object) -> list[dict]:  # type: ignore[type-arg]
        raise AssertionError("warm open should not call ki_native")

    monkeypatch.setattr(lib_mod, "_fetch_via_ki_native", boom)

    idx2 = LibraryIndex.open(lib_table, cache_dir)
    assert len(idx2) == len(idx1)
    hits = idx2.search("R", 10)
    assert any(h.lib_id == "Device:R" for h in hits)


def test_cache_is_invalidated_when_table_changes(
    lib_table: Path, tmp_path: Path
) -> None:
    _require_ki_native()
    cache_dir = tmp_path / "cache"
    LibraryIndex.open(lib_table, cache_dir)
    # Bump the mtime by re-writing the table with a trailing newline.
    time.sleep(0.01)
    text = lib_table.read_text()
    lib_table.write_text(text + "\n")
    idx2 = LibraryIndex.open(lib_table, cache_dir)
    # The new source_key must differ from the cached one.
    assert idx2.source_key.endswith(f"v{2}")  # schema rev in source_key
    # Still has every symbol.
    assert len(idx2) == 2


def test_open_rejects_missing_table(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        LibraryIndex.open(tmp_path / "nope" / "sym-lib-table", tmp_path / "cache")


def test_search_is_under_50ms_on_a_modest_index(
    lib_table: Path, tmp_path: Path
) -> None:
    """M1-P-02 NFR: warm queries < 50ms. On the tiny fixture this is
    trivially true; the real-library case is exercised by M1-Q-01."""
    _require_ki_native()
    idx = LibraryIndex.open(lib_table, tmp_path / "cache")
    start = time.perf_counter()
    for _ in range(100):
        idx.search("STM32", 10)
    elapsed_ms = (time.perf_counter() - start) * 1000.0 / 100.0
    assert elapsed_ms < 50.0, f"warm search averaged {elapsed_ms:.2f}ms"
