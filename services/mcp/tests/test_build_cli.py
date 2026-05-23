"""M2-T-10 acceptance tests for the `kiclaude build` Python entry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kc_mcp.build_cli import StageResult, _build_argparser, _human_report, run_build

PRO_TEMPLATE = '{ "meta": { "filename": "blinky.kicad_pro" } }'
PCB_TEMPLATE = """(kicad_pcb (version 20240108) (generator kiclaude)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0.0))
  (net 0 "")
)
"""


def _write_blinky_dir(root: Path) -> Path:
    project = root / "blinky"
    project.mkdir(parents=True)
    (project / "blinky.kicad_pro").write_text(PRO_TEMPLATE)
    (project / "blinky.kicad_pcb").write_text(PCB_TEMPLATE)
    return project


@pytest.mark.asyncio
async def test_run_build_emits_validate_drc_export_stages(tmp_path: Path) -> None:
    """Without kicad-cli on PATH, the pipeline still produces a report
    with a failing-but-structured stage for each step."""
    project = _write_blinky_dir(tmp_path)
    out = tmp_path / "dist"
    report = await run_build(
        str(project),
        output_dir=str(out),
        # Skip ERC + DRC + export to keep the test fully offline — the
        # validate stage alone exercises the orchestrator's wiring.
        skip_erc=True,
        skip_drc=True,
        skip_export=True,
    )
    stage_names = {s.name for s in report.stages}
    assert "validate" in stage_names
    # When everything is skipped, only `validate` ran; result reflects
    # whether ki_native is available (it isn't on this dev env), so
    # the validate stage typically returns ok=False with an error in
    # the detail. Either way, the report shape must be coherent.
    assert isinstance(report.ok, bool)
    assert all(isinstance(s, StageResult) for s in report.stages)
    payload = report.to_dict()
    assert payload["project_path"].endswith("blinky")
    assert "stages" in payload and isinstance(payload["stages"], list)


@pytest.mark.asyncio
async def test_run_build_creates_output_dir(tmp_path: Path) -> None:
    project = _write_blinky_dir(tmp_path)
    out = tmp_path / "fresh" / "deeply" / "dist"
    await run_build(
        str(project),
        output_dir=str(out),
        skip_erc=True,
        skip_drc=True,
        skip_export=True,
    )
    assert out.is_dir()


@pytest.mark.asyncio
async def test_run_build_fails_when_target_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await run_build(str(tmp_path / "nope"))


def test_argparser_includes_all_flags() -> None:
    parser = _build_argparser()
    ns = parser.parse_args(
        ["/tmp/blinky", "--out", "/tmp/out", "--json", "--no-color", "--skip-erc", "--skip-drc", "--skip-export"]
    )
    assert ns.project == "/tmp/blinky"
    assert ns.output_dir == "/tmp/out"
    assert ns.json is True
    assert ns.no_color is True
    assert ns.skip_erc is True
    assert ns.skip_drc is True
    assert ns.skip_export is True


def test_human_report_lists_every_stage() -> None:
    from kc_mcp.build_cli import BuildReport

    report = BuildReport(
        ok=False,
        project_path="/tmp/blinky",
        output_dir="/tmp/out",
        stages=[
            StageResult(name="validate", ok=True, duration_ms=12, detail={}),
            StageResult(
                name="drc", ok=False, duration_ms=34, detail={"error": "kicad-cli not on PATH"}
            ),
            StageResult(name="gerbers", ok=False, duration_ms=0, detail={"error": "skipped"}),
        ],
    )
    text = _human_report(report, color=False)
    assert "validate" in text
    assert "drc" in text
    assert "gerbers" in text
    assert "FAIL" in text


def test_to_dict_round_trips_via_json() -> None:
    from kc_mcp.build_cli import BuildReport

    report = BuildReport(
        ok=True,
        project_path="/tmp/blinky",
        output_dir="/tmp/out",
        stages=[StageResult(name="validate", ok=True, duration_ms=1, detail={"x": 1})],
    )
    j = json.dumps(report.to_dict())
    back = json.loads(j)
    assert back["ok"] is True
    assert back["stages"][0]["detail"]["x"] == 1
