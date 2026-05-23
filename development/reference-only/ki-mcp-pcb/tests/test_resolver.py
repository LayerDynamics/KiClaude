"""Tests for the MPN → footprint resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core.cir.models import Component
from ki_mcp_pcb_core.synthesis.resolver import (
    UnresolvedMPNError,
    load_registry,
    resolve_component,
    resolve_components,
)

REPO = Path(__file__).resolve().parents[1]


def test_registry_loads_demo_parts() -> None:
    reg = load_registry(REPO / "libs" / "footprints.yaml")
    assert "ESP32-S3-WROOM-1" in reg
    assert reg["ESP32-S3-WROOM-1"].symbol.startswith("RF_Module:")
    assert reg["GRM188R71C104KA01D"].lcsc == "C14663"


def test_explicit_overrides_win_over_registry(tmp_path: Path) -> None:
    reg_path = tmp_path / "fp.yaml"
    reg_path.write_text(
        "DUMMY:\n  symbol: 'Reg:Sym'\n  footprint: 'Reg:Fp'\n", encoding="utf-8"
    )
    comp = Component(refdes="U1", mpn="DUMMY", symbol="My:Sym", footprint="My:Fp")
    reg = load_registry(reg_path)
    resolved = resolve_component(comp, reg)
    assert resolved.symbol == "My:Sym"
    assert resolved.footprint == "My:Fp"


def test_registry_fallback_when_no_overrides(tmp_path: Path) -> None:
    reg_path = tmp_path / "fp.yaml"
    reg_path.write_text(
        "DUMMY:\n  symbol: 'Reg:Sym'\n  footprint: 'Reg:Fp'\n  value: '10k'\n",
        encoding="utf-8",
    )
    comp = Component(refdes="R1", mpn="DUMMY")
    reg = load_registry(reg_path)
    resolved = resolve_component(comp, reg)
    assert resolved.symbol == "Reg:Sym"
    assert resolved.footprint == "Reg:Fp"
    assert resolved.value == "10k"


def test_unresolved_mpn_fails_closed() -> None:
    comp = Component(refdes="U99", mpn="NOT-A-REAL-PART-001")
    with pytest.raises(UnresolvedMPNError) as exc:
        resolve_components([comp], registry_path=Path("/dev/null"))
    assert "NOT-A-REAL-PART-001" in str(exc.value)
    assert "U99" in str(exc.value)


def test_resolve_components_batch() -> None:
    comps = [
        Component(refdes="U1", mpn="ESP32-S3-WROOM-1"),
        Component(refdes="C1", mpn="GRM188R71C104KA01D"),
    ]
    resolved = resolve_components(comps)
    assert {r.refdes for r in resolved} == {"U1", "C1"}
    assert all(":" in r.symbol and ":" in r.footprint for r in resolved)
