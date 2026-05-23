"""M1-P-03 acceptance tests for the kicad-cli ERC wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiconnector.erc import ErcIssue, parse_erc_report, run_erc
from kiconnector.main import app

KICAD_AVAILABLE = shutil.which("kicad-cli") is not None


def test_parse_erc_report_sheets_shape() -> None:
    payload = {
        "$schema": "https://kicad.org/erc/v1",
        "source": "blinky.kicad_sch",
        "sheets": [
            {
                "uuid_path": "/aaaa-bbbb",
                "path": "/blinky",
                "violations": [
                    {
                        "severity": "warning",
                        "type": "no_connect",
                        "description": "Pin not connected",
                        "items": [
                            {"uuid": "u1", "pos_x": 50.0, "pos_y": 60.0},
                            {"uuid": "u2", "pos_x": 51.0, "pos_y": 61.0},
                        ],
                    },
                    {
                        "severity": "error",
                        "type": "label_dangling",
                        "description": "Dangling label",
                        "items": [{"uuid": "u3", "pos_x": 30.0, "pos_y": 30.0}],
                    },
                ],
            }
        ],
    }
    issues = parse_erc_report(payload)
    assert len(issues) == 3
    assert issues[0].severity == "warning"
    assert issues[0].sheet == "/aaaa-bbbb"
    assert issues[0].position_mm == (50.0, 60.0)
    assert issues[0].type == "no_connect"
    assert issues[-1].severity == "error"
    assert issues[-1].type == "label_dangling"


def test_parse_erc_report_flat_violations_fallback() -> None:
    payload = {
        "violations": [
            {
                "severity": "warning",
                "code": "missing_unit",
                "message": "Symbol missing unit",
                "items": [{"pos_x": 10.0, "pos_y": 20.0}],
            }
        ]
    }
    issues = parse_erc_report(payload)
    assert len(issues) == 1
    assert issues[0].type == "missing_unit"
    assert issues[0].description == "Symbol missing unit"
    assert issues[0].sheet == ""


def test_parse_erc_report_no_items_emits_zero_position() -> None:
    payload = {
        "sheets": [
            {
                "uuid_path": "/x",
                "violations": [
                    {"severity": "ignore", "type": "info", "description": "n/a"}
                ],
            }
        ]
    }
    issues = parse_erc_report(payload)
    assert len(issues) == 1
    assert issues[0].position_mm == (0.0, 0.0)


def test_erc_issue_to_dict_round_trips() -> None:
    issue = ErcIssue(
        severity="error",
        sheet="/s",
        position_mm=(1.0, 2.0),
        type="no_net",
        description="d",
    )
    d = issue.to_dict()
    assert d == {
        "severity": "error",
        "sheet": "/s",
        "position_mm": [1.0, 2.0],
        "type": "no_net",
        "description": "d",
    }


@pytest.mark.asyncio
async def test_run_erc_returns_typed_error_when_target_missing(
    tmp_path: Path,
) -> None:
    report = await run_erc(tmp_path / "does-not-exist")
    assert report.ok is False
    assert report.issues == []
    assert "not found" in (report.error or "")


@pytest.mark.asyncio
async def test_run_erc_returns_503_style_envelope_when_kicad_cli_missing(
    tmp_path: Path,
) -> None:
    """Without kicad-cli on PATH the wrapper still returns an
    ErcReport (rather than raising)."""
    file = tmp_path / "demo.kicad_sch"
    file.write_text("(kicad_sch)")
    report = await run_erc(file, kicad_cli_binary="kicad-cli-definitely-not-installed")
    assert report.ok is False
    assert report.issues == []
    assert "not on PATH" in (report.error or "")


def test_post_tools_erc_missing_kicad_cli_returns_503(
    tmp_path: Path,
) -> None:
    """`POST /tools/erc` surfaces "kicad-cli not installed" as 503
    so the gateway can report a missing dep cleanly."""
    if KICAD_AVAILABLE:
        pytest.skip("kicad-cli is installed; skipping the missing-binary path")
    f = tmp_path / "demo.kicad_sch"
    f.write_text("(kicad_sch)")
    client = TestClient(app)
    resp = client.post("/tools/erc", json={"project_path": str(f)})
    assert resp.status_code == 503


def test_post_tools_erc_target_not_found_returns_200_with_error_envelope(
    tmp_path: Path,
) -> None:
    """If kicad-cli is installed and the target file is missing,
    the wrapper returns 200 with `ok:false, error:"..."` so the UI
    can render the diagnostic without parsing HTTP semantics."""
    if not KICAD_AVAILABLE:
        pytest.skip("kicad-cli not on PATH")
    client = TestClient(app)
    resp = client.post(
        "/tools/erc",
        json={"project_path": str(tmp_path / "nope.kicad_sch")},
    )
    # When binary exists but target doesn't, the body sets ok=false.
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        body = resp.json()
        assert body["ok"] is False
        assert "not found" in (body.get("error") or "")
