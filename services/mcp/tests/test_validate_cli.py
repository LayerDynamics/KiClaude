"""M1-T-09 tests for the `kiclaude validate` CLI entry point.

Tests cover the programmatic `run_validate` coroutine + the
`main()` argparse glue. The kicad-cli ERC bridge is mocked via
`monkeypatch.setattr` so the suite runs without `kicad-cli` on
PATH.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kc_mcp import validate_cli
from kc_mcp.validate_cli import (
    ValidateReport,
    _human_report,
    _resolve_project_dir,
    _summarize,
    main,
    run_validate,
)


def _seed_blinky_project() -> dict[str, Any]:
    """A minimal KCIR project that the validators run cleanly on."""
    return {
        "kcir_version": "0.2.0",
        "name": "blinky-cli",
        "schematic": {
            "sheets": [
                {
                    "uuid": "sheet-root",
                    "name": "blinky-cli",
                    "file": "blinky-cli.kicad_sch",
                    "parent": None,
                    "position_mm": [0.0, 0.0],
                    "size_mm": [0.0, 0.0],
                    "pins": [],
                }
            ],
            "symbols": [
                {
                    "uuid": "sym-R1",
                    "refdes": "R1",
                    "value": "10k",
                    "lib_id": "Device:R",
                    "footprint": "Resistor_SMD:R_0603_1608Metric",
                    "sheet_uuid": "sheet-root",
                    "position_mm": [0.0, 0.0],
                    "rotation_deg": 0.0,
                    "mirror": "",
                    "in_bom": True,
                    "on_board": True,
                    "dnp": False,
                    "pins": [],
                    "fields": [],
                    "unit": 1,
                    "convert": 1,
                }
            ],
            "wires": [],
            "junctions": [],
            "labels": [],
            "no_connects": [],
            "buses": [],
            "lib_symbols": [],
        },
        "pcb": {
            "version": 20240108,
            "generator": "kiclaude",
            "thickness_mm": 1.6,
            "paper": "A4",
            "pad_to_mask_clearance_mm": 0.0,
            "layers": [
                {"id": 0, "name": "F.Cu", "kind": "signal"},
            ],
            "footprints": [],
            "tracks": [],
            "vias": [],
            "zones": [],
            "outline": {"points_mm": [], "cutouts": []},
            "drawings": [],
            "nets": [],
        },
        "libraries": {"symbol_libs": [], "footprint_libs": []},
        "stackup": {
            "layers": [],
            "power_plane_layers": [],
            "controlled_impedance": False,
            "board_thickness_mm": 0.0,
            "finish": "",
        },
        "design_rules": {
            "clearance_mm": 0.0,
            "trace_width_mm": 0.0,
            "via_drill_mm": 0.0,
            "via_diameter_mm": 0.0,
            "uvia_drill_mm": 0.0,
            "uvia_diameter_mm": 0.0,
            "allow_microvias": False,
            "allow_blind_buried_vias": False,
        },
        "net_classes": [],
        "fab_target": None,
        "bom_policy": {
            "preferred_distributors": [],
            "max_unit_price_usd": None,
            "require_in_stock": False,
            "require_jlc_assembly": False,
            "region": "",
        },
        "metadata": {
            "title": "blinky-cli",
            "revision": "",
            "company": "",
            "date": "",
            "comment_1": "",
            "comment_2": "",
            "comment_3": "",
            "comment_4": "",
        },
    }


def _seed_broken_project() -> dict[str, Any]:
    """A project that trips KC001 — a symbol without a refdes."""
    proj = _seed_blinky_project()
    proj["schematic"]["symbols"][0]["refdes"] = ""
    return proj


@pytest.fixture()
def patched_loader(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace `_load_project` so the test never needs PyO3. Tests
    mutate the dict to control which KCIR the validator sees."""
    holder: dict[str, Any] = {"project": _seed_blinky_project()}

    def fake_load(_p: Path) -> dict[str, Any]:
        return holder["project"]

    monkeypatch.setattr(validate_cli, "_load_project", fake_load)
    return holder


@pytest.fixture()
def patched_erc(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace `_run_erc` so the suite never needs kicad-cli."""
    holder: dict[str, Any] = {
        "ok": True,
        "issues": [],
        "error": None,
    }

    async def fake_run_erc(
        _dir: Path, _skip: bool
    ) -> tuple[bool, list[dict[str, Any]], str | None]:
        return holder["ok"], holder["issues"], holder["error"]

    monkeypatch.setattr(validate_cli, "_run_erc", fake_run_erc)
    return holder


def _project_dir(tmp_path: Path, name: str = "proj") -> Path:
    p = tmp_path / name
    p.mkdir()
    # `_resolve_project_dir` requires a real directory, so just give
    # it one — the loader is patched.
    return p


def test_resolve_project_dir_accepts_directory(tmp_path: Path) -> None:
    p = _project_dir(tmp_path)
    assert _resolve_project_dir(p) == p.resolve()


def test_resolve_project_dir_walks_up_from_kicad_pro(tmp_path: Path) -> None:
    p = _project_dir(tmp_path, "demo")
    pro = p / "demo.kicad_pro"
    pro.write_text("(kicad_pro)")
    assert _resolve_project_dir(pro) == p.resolve()


def test_resolve_project_dir_rejects_non_kicad_file(tmp_path: Path) -> None:
    bad = tmp_path / "notes.txt"
    bad.write_text("hi")
    with pytest.raises(FileNotFoundError):
        _resolve_project_dir(bad)


def test_summarize_combines_kc_and_erc_severities() -> None:
    summary = _summarize(
        [
            {"severity": "error", "code": "KC001"},
            {"severity": "warning", "code": "KC003"},
        ],
        [
            {"severity": "error", "type": "no_connect"},
            {"severity": "exclusion", "type": "ignored"},
        ],
    )
    assert summary == {"error": 2, "warning": 1, "info": 1}


async def test_run_validate_returns_pass_for_clean_project(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    patched_erc: dict[str, Any],
) -> None:
    report = await run_validate(str(_project_dir(tmp_path)))
    assert report.ok is True
    assert report.summary["error"] == 0
    assert report.erc_issues == []
    assert report.erc_ok is True


async def test_run_validate_flags_kc001_failures(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    patched_erc: dict[str, Any],
) -> None:
    patched_loader["project"] = _seed_broken_project()
    report = await run_validate(str(_project_dir(tmp_path)))
    assert report.ok is False
    assert any(f["code"] == "KC001" for f in report.findings)
    assert report.summary["error"] >= 1


async def test_run_validate_surfaces_erc_errors(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    patched_erc: dict[str, Any],
) -> None:
    patched_erc["issues"] = [
        {
            "severity": "error",
            "sheet": "/root",
            "position_mm": [0.0, 0.0],
            "type": "two_outputs",
            "description": "conflict",
        }
    ]
    report = await run_validate(str(_project_dir(tmp_path)))
    assert report.ok is False
    assert report.summary["error"] >= 1


async def test_run_validate_skip_erc_bypasses_subprocess(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `skip_erc=True`, `_run_erc` is called with `skip=True` —
    and we wire it to assert that."""
    captured: dict[str, bool] = {"skip": False}

    async def fake_run_erc(_dir: Path, skip: bool) -> tuple[bool, list[dict[str, Any]], str | None]:
        captured["skip"] = skip
        return True, [], None

    monkeypatch.setattr(validate_cli, "_run_erc", fake_run_erc)
    report = await run_validate(str(_project_dir(tmp_path)), skip_erc=True)
    assert captured["skip"] is True
    assert report.erc_issues == []


async def test_run_validate_reports_load_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_erc: dict[str, Any],
) -> None:
    def boom(_p: Path) -> dict[str, Any]:
        raise RuntimeError("ki_native missing")

    monkeypatch.setattr(validate_cli, "_load_project", boom)
    report = await run_validate(str(_project_dir(tmp_path)))
    assert report.ok is False
    assert report.erc_ok is False
    assert "ki_native" in (report.erc_error or "")
    assert report.summary["error"] == 1


def test_human_report_renders_pass(
    tmp_path: Path,
) -> None:
    report = ValidateReport(
        ok=True,
        project_path=str(_project_dir(tmp_path)),
        project_name="demo",
        findings=[],
        erc_issues=[],
        summary={"error": 0, "warning": 0, "info": 0},
    )
    text = _human_report(report, color=False)
    assert "PASS" in text
    assert "demo" in text


def test_human_report_renders_findings(tmp_path: Path) -> None:
    report = ValidateReport(
        ok=False,
        project_path=str(_project_dir(tmp_path)),
        project_name="bad",
        findings=[
            {
                "code": "KC001",
                "severity": "error",
                "message": "missing refdes",
                "target_uuid": "u-1",
            }
        ],
        erc_issues=[],
        summary={"error": 1, "warning": 0, "info": 0},
    )
    text = _human_report(report, color=False)
    assert "FAIL" in text
    assert "KC001" in text
    assert "missing refdes" in text


def test_main_exits_zero_on_pass(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    patched_erc: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([str(_project_dir(tmp_path)), "--no-color"])
    captured = capsys.readouterr()
    assert code == 0
    assert "PASS" in captured.out


def test_main_exits_one_on_kc_error(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    patched_erc: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    patched_loader["project"] = _seed_broken_project()
    code = main([str(_project_dir(tmp_path)), "--no-color"])
    captured = capsys.readouterr()
    assert code == 1
    assert "FAIL" in captured.out


def test_main_json_emits_structured_report(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    patched_erc: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([str(_project_dir(tmp_path)), "--json"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert "summary" in payload
    assert payload["project_name"] == "blinky-cli"


def test_main_exits_two_for_missing_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([str(tmp_path / "does-not-exist")])
    captured = capsys.readouterr()
    assert code == 2
    assert "kiclaude validate" in captured.err


def test_main_skip_erc_flag(
    tmp_path: Path,
    patched_loader: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, bool] = {"skip": False}

    async def fake_run_erc(_dir: Path, skip: bool) -> tuple[bool, list[dict[str, Any]], str | None]:
        captured["skip"] = skip
        return True, [], None

    monkeypatch.setattr(validate_cli, "_run_erc", fake_run_erc)
    code = main([str(_project_dir(tmp_path)), "--skip-erc", "--no-color"])
    assert code == 0
    assert captured["skip"] is True
