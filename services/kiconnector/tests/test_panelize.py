"""M2-P-07 acceptance tests for the KiKit panelize wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiconnector.kikit import run_panelize
from kiconnector.main import app

KIKIT_AVAILABLE = shutil.which("kikit") is not None


@pytest.mark.asyncio
async def test_run_panelize_rejects_missing_target(tmp_path: Path) -> None:
    result = await run_panelize(
        tmp_path / "missing.kicad_pcb",
        tmp_path / "panel.kicad_pcb",
        config={"layout": {"rows": 1, "cols": 1}},
    )
    assert result.ok is False
    assert "PCB not found" in (result.error or "")


@pytest.mark.asyncio
async def test_run_panelize_rejects_non_pcb_target(tmp_path: Path) -> None:
    sch = tmp_path / "demo.kicad_sch"
    sch.write_text("(kicad_sch)")
    result = await run_panelize(
        sch,
        tmp_path / "panel.kicad_pcb",
        config={"layout": {"rows": 1, "cols": 1}},
    )
    assert result.ok is False
    assert "must be a .kicad_pcb" in (result.error or "")


@pytest.mark.asyncio
async def test_run_panelize_envelope_when_kikit_missing(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    result = await run_panelize(
        pcb,
        tmp_path / "panel.kicad_pcb",
        config={"layout": {"rows": 1, "cols": 1}},
        kikit_binary="kikit-definitely-not-installed",
    )
    assert result.ok is False
    assert "not on PATH" in (result.error or "")


@pytest.mark.asyncio
async def test_run_panelize_requires_config_or_preset(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    result = await run_panelize(pcb, tmp_path / "panel.kicad_pcb")
    assert result.ok is False
    assert "config" in (result.error or "")


def test_post_tools_panelize_missing_kikit_returns_503(tmp_path: Path) -> None:
    if KIKIT_AVAILABLE:
        pytest.skip("kikit is installed; skipping the missing-binary path")
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    client = TestClient(app)
    resp = client.post(
        "/tools/panelize",
        json={
            "pcb_path": str(pcb),
            "output_path": str(tmp_path / "panel.kicad_pcb"),
            "config": {"layout": {"rows": 1, "cols": 1}},
        },
    )
    assert resp.status_code == 503
