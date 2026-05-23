"""M2-P-01 acceptance tests for the kicad-cli DRC wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiconnector.drc import DrcIssue, parse_drc_report, run_drc
from kiconnector.main import app

KICAD_AVAILABLE = shutil.which("kicad-cli") is not None


def test_parse_drc_report_violations_shape() -> None:
    payload = {
        "$schema": "https://kicad.org/drc/v1",
        "source": "blinky.kicad_pcb",
        "violations": [
            {
                "severity": "error",
                "type": "clearance",
                "description": "Track too close to pad",
                "items": [
                    {"uuid": "u1", "pos_x": 50.0, "pos_y": 60.0, "layer": "F.Cu"},
                    {"uuid": "u2", "pos_x": 51.0, "pos_y": 61.0, "layer": "F.Cu"},
                ],
            },
            {
                "severity": "warning",
                "type": "courtyards_overlap",
                "description": "Courtyards overlap",
                "items": [{"uuid": "u3", "pos_x": 30.0, "pos_y": 30.0, "layer": "F.Fab"}],
            },
        ],
        "unconnected_items": [
            {
                "severity": "error",
                "type": "missing_connection",
                "description": "Net GND has unconnected pad",
                "items": [{"uuid": "u4", "pos_x": 10.0, "pos_y": 10.0, "layer": "F.Cu"}],
            }
        ],
        "schematic_parity": [],
    }
    issues = parse_drc_report(payload)
    assert len(issues) == 4
    assert issues[0].severity == "error"
    assert issues[0].layer == "F.Cu"
    assert issues[0].position_mm == (50.0, 60.0)
    assert issues[0].type == "clearance"
    # The unconnected_items bucket falls through with its own type.
    assert any(i.type == "missing_connection" for i in issues)


def test_parse_drc_report_no_items_emits_zero_position() -> None:
    payload = {
        "violations": [
            {"severity": "warning", "type": "info", "description": "n/a"}
        ]
    }
    issues = parse_drc_report(payload)
    assert len(issues) == 1
    assert issues[0].position_mm == (0.0, 0.0)
    assert issues[0].layer == ""


def test_drc_issue_to_dict_round_trips() -> None:
    issue = DrcIssue(
        severity="error",
        layer="F.Cu",
        position_mm=(1.0, 2.0),
        type="clearance",
        description="d",
    )
    d = issue.to_dict()
    assert d == {
        "severity": "error",
        "layer": "F.Cu",
        "position_mm": [1.0, 2.0],
        "type": "clearance",
        "description": "d",
    }


@pytest.mark.asyncio
async def test_run_drc_rejects_non_pcb_target(tmp_path: Path) -> None:
    sch = tmp_path / "demo.kicad_sch"
    sch.write_text("(kicad_sch)")
    report = await run_drc(sch)
    assert report.ok is False
    assert "must be a .kicad_pcb" in (report.error or "")


@pytest.mark.asyncio
async def test_run_drc_returns_envelope_when_kicad_cli_missing(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    report = await run_drc(pcb, kicad_cli_binary="kicad-cli-definitely-not-installed")
    assert report.ok is False
    assert report.issues == []
    assert "not on PATH" in (report.error or "")


def test_post_tools_drc_missing_kicad_cli_returns_503(tmp_path: Path) -> None:
    if KICAD_AVAILABLE:
        pytest.skip("kicad-cli is installed; skipping the missing-binary path")
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    client = TestClient(app)
    resp = client.post("/tools/drc", json={"pcb_path": str(pcb)})
    assert resp.status_code == 503


def test_severity_filter_drops_warnings_when_min_is_error() -> None:
    payload = {
        "violations": [
            {"severity": "warning", "type": "courtyards_overlap", "items": []},
            {"severity": "error", "type": "clearance", "items": []},
        ]
    }
    issues = parse_drc_report(payload)
    from kiconnector.drc import _filter_severity

    only_errors = _filter_severity(issues, "error")
    assert {i.severity for i in only_errors} == {"error"}
