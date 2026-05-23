"""M2-P-03 acceptance tests for the kicad-cli BOM wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiconnector.bom import (
    DEFAULT_FIELDS,
    BomRow,
    format_grouped_csv,
    group_rows,
    parse_bom_csv,
    run_bom,
)
from kiconnector.main import app

KICAD_AVAILABLE = shutil.which("kicad-cli") is not None


def test_parse_bom_csv_grouped_rows_keep_quantity() -> None:
    text = (
        "Reference,Value,Footprint,MPN,Manufacturer,Description,Datasheet,Quantity\n"
        "C1,100nF,Capacitor_SMD:C_0603_1608Metric,,,,,1\n"
        '"R1,R2",10k,Resistor_SMD:R_0603_1608Metric,YAGEO-RC0603,YAGEO,Resistor,,2\n'
    )
    rows = parse_bom_csv(text)
    assert len(rows) == 2
    assert rows[0].reference == "C1"
    assert rows[0].quantity == 1
    assert rows[1].reference == "R1,R2"
    assert rows[1].quantity == 2
    assert rows[1].mpn == "YAGEO-RC0603"


def test_parse_bom_csv_flat_rows_infer_quantity_from_reference_list() -> None:
    text = (
        "Reference,Value,Footprint\n"
        '"R1,R2,R3",10k,Resistor_SMD:R_0603\n'
        "C1,100nF,Capacitor_SMD:C_0603\n"
    )
    rows = parse_bom_csv(text)
    assert rows[0].quantity == 3, "comma-separated refdes infers count"
    assert rows[1].quantity == 1


def test_group_rows_collapses_by_value_footprint_mpn() -> None:
    rows = [
        BomRow(reference="R1", value="10k", footprint="R_0603", mpn="A"),
        BomRow(reference="R2", value="10k", footprint="R_0603", mpn="A"),
        BomRow(reference="R3", value="10k", footprint="R_0603", mpn="B"),
        BomRow(reference="C1", value="100nF", footprint="C_0603"),
    ]
    grouped = group_rows(rows)
    assert len(grouped) == 3, "two MPN variants for 10k + one cap = 3 buckets"
    by_mpn = {r.mpn: r for r in grouped if r.value == "10k"}
    assert by_mpn["A"].quantity == 2
    assert by_mpn["A"].reference == "R1,R2"
    assert by_mpn["B"].quantity == 1


def test_format_grouped_csv_emits_default_field_order() -> None:
    rows = [BomRow(reference="C1", value="100nF", footprint="C_0603", quantity=1)]
    text = format_grouped_csv(rows)
    header = text.splitlines()[0]
    assert header == ",".join(DEFAULT_FIELDS)


@pytest.mark.asyncio
async def test_run_bom_rejects_missing_target(tmp_path: Path) -> None:
    report = await run_bom(tmp_path / "does-not-exist.kicad_sch", tmp_path)
    assert report.ok is False
    assert "not found" in (report.error or "")


@pytest.mark.asyncio
async def test_run_bom_rejects_non_sch_target(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    report = await run_bom(pcb, tmp_path)
    assert report.ok is False
    assert "must be a .kicad_sch" in (report.error or "")


@pytest.mark.asyncio
async def test_run_bom_envelope_when_kicad_cli_missing(tmp_path: Path) -> None:
    sch = tmp_path / "demo.kicad_sch"
    sch.write_text("(kicad_sch)")
    report = await run_bom(
        sch,
        tmp_path / "out",
        kicad_cli_binary="kicad-cli-definitely-not-installed",
    )
    assert report.ok is False
    assert "not on PATH" in (report.error or "")


def test_post_tools_bom_missing_kicad_cli_returns_503(tmp_path: Path) -> None:
    if KICAD_AVAILABLE:
        pytest.skip("kicad-cli is installed; skipping the missing-binary path")
    sch = tmp_path / "demo.kicad_sch"
    sch.write_text("(kicad_sch)")
    client = TestClient(app)
    resp = client.post(
        "/tools/bom",
        json={"sch_path": str(sch), "output_dir": str(tmp_path / "out")},
    )
    assert resp.status_code == 503
