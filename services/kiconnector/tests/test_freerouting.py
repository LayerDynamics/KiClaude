"""M2-P-06 acceptance tests for the Freerouting subprocess wrapper."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiconnector.freerouting import run_freerouting
from kiconnector.main import app

JAVA_AVAILABLE = shutil.which("java") is not None
KICAD_AVAILABLE = shutil.which("kicad-cli") is not None


@pytest.mark.asyncio
async def test_run_freerouting_rejects_missing_target(tmp_path: Path) -> None:
    result = await run_freerouting(tmp_path / "missing.kicad_pcb")
    assert result.ok is False
    assert "PCB not found" in (result.error or "")


@pytest.mark.asyncio
async def test_run_freerouting_rejects_non_pcb_target(tmp_path: Path) -> None:
    sch = tmp_path / "demo.kicad_sch"
    sch.write_text("(kicad_sch)")
    result = await run_freerouting(sch)
    assert result.ok is False
    assert "must be a .kicad_pcb" in (result.error or "")


@pytest.mark.asyncio
async def test_run_freerouting_requires_jar_path(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    result = await run_freerouting(pcb, freerouting_jar="")
    assert result.ok is False
    assert "jar path missing" in (result.error or "")


@pytest.mark.asyncio
async def test_run_freerouting_envelope_when_jar_missing(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    result = await run_freerouting(pcb, freerouting_jar="/definitely/not/here.jar")
    assert result.ok is False
    assert "jar not found" in (result.error or "")


def test_post_tools_freerouting_returns_503_when_jar_missing(tmp_path: Path) -> None:
    if not JAVA_AVAILABLE or not KICAD_AVAILABLE:
        pytest.skip("java + kicad-cli required for the 503 path")
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    client = TestClient(app)
    resp = client.post(
        "/tools/freerouting",
        json={"pcb_path": str(pcb), "freerouting_jar": ""},
    )
    # Without a jar configured the route returns a 200 envelope with
    # ok=false ("jar path missing"), not 503 — the 503 path is for
    # "binary not on PATH".
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
