"""JLC catalog lookup + live sourcing enrichment."""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.sourcing import check_sourcing
from ki_mcp_pcb_core.sourcing.jlc import (
    JLCLookupError,
    _reset_cache_for_tests,
    is_available,
    lookup_by_lcsc,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# ---------------------------------------------------------------------------
# Fixture: small fake JLC catalog
# ---------------------------------------------------------------------------


_FAKE_CATALOG = """\
LCSC Part Number,MFR.Part,Description,Package,Price (USD),Stock,Library Type
C14663,GRM188R71C104KA01D,"CAP CER 100NF 16V X7R 0603",0603,0.0019,1200000,Basic
C28323,GRM21BR60J106KE19L,"CAP CER 10UF 6.3V X5R 0805",0805,0.0102,500000,Basic
C8763,LAN8720AI-CP-TR,"100BASE-T PHY",QFN-24,1.2400,5000,Extended
"""


@pytest.fixture
def fake_catalog(tmp_path, monkeypatch):
    csv_path = tmp_path / "jlc_parts.csv"
    csv_path.write_text(_FAKE_CATALOG, encoding="utf-8")
    monkeypatch.setenv("KIMP_JLC_CSV", str(csv_path))
    _reset_cache_for_tests()
    yield csv_path
    _reset_cache_for_tests()


# ---------------------------------------------------------------------------
# lookup_by_lcsc
# ---------------------------------------------------------------------------


def test_lookup_known_lcsc(fake_catalog) -> None:
    part = lookup_by_lcsc("C14663")
    assert part is not None
    assert part.mpn == "GRM188R71C104KA01D"
    assert part.unit_price_usd == 0.0019
    assert part.stock == 1_200_000
    assert part.library_type == "Basic"


def test_lookup_unknown_lcsc_returns_none(fake_catalog) -> None:
    assert lookup_by_lcsc("C99999999") is None


def test_lookup_strips_whitespace(fake_catalog) -> None:
    part = lookup_by_lcsc("  C28323  ")
    assert part is not None
    assert part.mpn == "GRM21BR60J106KE19L"


def test_lookup_raises_when_catalog_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KIMP_JLC_CSV", str(tmp_path / "no_such.csv"))
    _reset_cache_for_tests()
    with pytest.raises(JLCLookupError):
        lookup_by_lcsc("C14663")
    _reset_cache_for_tests()


def test_is_available_reflects_csv_presence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KIMP_JLC_CSV", str(tmp_path / "no_such.csv"))
    assert is_available() is False
    csv = tmp_path / "yes.csv"
    csv.write_text("LCSC Part Number\n", encoding="utf-8")
    monkeypatch.setenv("KIMP_JLC_CSV", str(csv))
    assert is_available() is True


# ---------------------------------------------------------------------------
# check_sourcing enrichment
# ---------------------------------------------------------------------------


def test_check_sourcing_without_live_jlc_unchanged() -> None:
    """Default behaviour is unchanged (registry-only). No live data fields."""
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    report = check_sourcing(board)
    for e in report.entries:
        assert e.unit_price_usd is None
        assert e.stock is None


def test_check_sourcing_with_live_jlc_fills_price_and_stock(fake_catalog) -> None:
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    report = check_sourcing(board, include_live_jlc=True)
    by_mpn = {e.mpn: e for e in report.entries}
    cap = by_mpn["GRM188R71C104KA01D"]
    assert cap.unit_price_usd == 0.0019
    assert cap.stock == 1_200_000
    assert "JLC live" in cap.note


def test_check_sourcing_live_no_lcsc_gets_no_enrichment(fake_catalog) -> None:
    """Components without an LCSC mapping aren't enriched (nothing to query)."""
    board = parse_yaml(EXAMPLES / "blinky.yaml")
    report = check_sourcing(board, include_live_jlc=True)
    esp = next(e for e in report.entries if e.mpn == "ESP32-S3-WROOM-1")
    assert esp.lcsc is None
    assert esp.unit_price_usd is None


def test_check_sourcing_live_without_catalog_degrades_gracefully(
    tmp_path, monkeypatch
) -> None:
    """When the catalog file doesn't exist, the report still completes."""
    monkeypatch.setenv("KIMP_JLC_CSV", str(tmp_path / "ghost.csv"))
    _reset_cache_for_tests()
    try:
        board = parse_yaml(EXAMPLES / "blinky.yaml")
        report = check_sourcing(board, include_live_jlc=True)
        assert report.ok
        # No live data filled in
        for e in report.entries:
            assert e.unit_price_usd is None
    finally:
        _reset_cache_for_tests()
