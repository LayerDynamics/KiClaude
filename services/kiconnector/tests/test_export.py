"""M2-P-02 acceptance tests for the kicad-cli fab export wrappers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiconnector.export import (
    DEFAULT_GERBER_LAYERS,
    export_drill,
    export_gerbers,
    export_pos,
)
from kiconnector.main import app

KICAD_AVAILABLE = shutil.which("kicad-cli") is not None


def test_default_gerber_layers_cover_jlc_minimum() -> None:
    """JLC requires F.Cu, B.Cu, F.Mask, B.Mask, F.SilkS, B.SilkS, and
    Edge.Cuts at minimum. The default tuple must contain them all."""
    required = {"F.Cu", "B.Cu", "F.Mask", "B.Mask", "F.SilkS", "B.SilkS", "Edge.Cuts"}
    assert required.issubset(set(DEFAULT_GERBER_LAYERS))


@pytest.mark.asyncio
async def test_export_gerbers_rejects_missing_target(tmp_path: Path) -> None:
    artifact = await export_gerbers(
        tmp_path / "does-not-exist.kicad_pcb",
        tmp_path / "out",
    )
    assert artifact.ok is False
    assert "PCB not found" in (artifact.error or "")


@pytest.mark.asyncio
async def test_export_gerbers_rejects_non_pcb_extension(tmp_path: Path) -> None:
    sch = tmp_path / "demo.kicad_sch"
    sch.write_text("(kicad_sch)")
    artifact = await export_gerbers(sch, tmp_path / "out")
    assert artifact.ok is False
    assert "must be .kicad_pcb" in (artifact.error or "")


@pytest.mark.asyncio
async def test_export_gerbers_envelope_when_kicad_cli_missing(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    artifact = await export_gerbers(
        pcb,
        tmp_path / "out",
        kicad_cli_binary="kicad-cli-definitely-not-installed",
    )
    assert artifact.ok is False
    assert "not on PATH" in (artifact.error or "")


@pytest.mark.asyncio
async def test_export_drill_envelope_when_kicad_cli_missing(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    artifact = await export_drill(
        pcb,
        tmp_path / "out",
        kicad_cli_binary="kicad-cli-definitely-not-installed",
    )
    assert artifact.ok is False
    assert "not on PATH" in (artifact.error or "")


@pytest.mark.asyncio
async def test_export_pos_rejects_bad_side(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    artifact = await export_pos(pcb, tmp_path / "out", side="upside-down")
    assert artifact.ok is False
    assert "side must be one of" in (artifact.error or "")


@pytest.mark.asyncio
async def test_export_creates_output_dir(tmp_path: Path) -> None:
    """The wrappers must mkdir the output dir before invoking kicad-cli."""
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    out = tmp_path / "deeply" / "nested" / "out"
    # The kicad-cli binary is missing here; we only assert the dir
    # creation step ran. (The error envelope is the "missing on PATH"
    # message, not the mkdir failure.)
    artifact = await export_gerbers(
        pcb, out, kicad_cli_binary="kicad-cli-definitely-not-installed"
    )
    assert out.is_dir(), "output dir should be created even when kicad-cli is absent"
    assert artifact.ok is False


def test_post_tools_gerbers_missing_kicad_cli_returns_503(tmp_path: Path) -> None:
    if KICAD_AVAILABLE:
        pytest.skip("kicad-cli is installed; skipping the missing-binary path")
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    client = TestClient(app)
    resp = client.post(
        "/tools/gerbers",
        json={"pcb_path": str(pcb), "output_dir": str(tmp_path / "out")},
    )
    assert resp.status_code == 503


def test_post_tools_drill_missing_kicad_cli_returns_503(tmp_path: Path) -> None:
    if KICAD_AVAILABLE:
        pytest.skip("kicad-cli is installed; skipping the missing-binary path")
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    client = TestClient(app)
    resp = client.post(
        "/tools/drill",
        json={"pcb_path": str(pcb), "output_dir": str(tmp_path / "out")},
    )
    assert resp.status_code == 503


def test_post_tools_pos_rejects_invalid_side(tmp_path: Path) -> None:
    pcb = tmp_path / "demo.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    client = TestClient(app)
    resp = client.post(
        "/tools/pos",
        json={
            "pcb_path": str(pcb),
            "output_dir": str(tmp_path / "out"),
            "side": "upside-down",
        },
    )
    # Pydantic catches the bad regex before the handler runs.
    assert resp.status_code == 422
