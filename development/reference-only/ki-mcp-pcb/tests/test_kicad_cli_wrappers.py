"""Tests for the kicad-cli wrapper surface.

Subprocess is mocked via ``set_runner_for_tests`` so the suite runs
without KiCad. Each test asserts the *invocation contract* — the
arguments passed to kicad-cli, the report file path, the parsing of
KiCad's JSON output.

A separate CI job (see .github/workflows/ci.yml::kicad-build) installs
KiCad on Ubuntu and runs the real end-to-end pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from ki_mcp_pcb_core import _kicad_cli as kc
from ki_mcp_pcb_core.export.bom import build_bom_rows, write_bom_csv
from ki_mcp_pcb_core.export.fab_package import export_fab_package
from ki_mcp_pcb_core.export.gerbers import export_drill, export_gerbers
from ki_mcp_pcb_core.export.pick_and_place import export_pick_and_place
from ki_mcp_pcb_core.export.step import export_step
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.validation.drc import run_drc
from ki_mcp_pcb_core.validation.erc import run_erc

# ---------------------------------------------------------------------------
# Mock runner infrastructure
# ---------------------------------------------------------------------------


class RecordingRunner:
    """Captures kicad-cli invocations and emits canned report files."""

    def __init__(self, *, write_reports: dict[str, dict] | None = None,
                 stdout: str = "", returncode: int = 0):
        self.calls: list[list[str]] = []
        self.write_reports = write_reports or {}
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, argv):
        self.calls.append(list(argv))
        # If the args include "-o <path>", and we've been told to fake a report
        # at that path, write it.
        if "-o" in argv:
            idx = list(argv).index("-o")
            path = Path(argv[idx + 1])
            report_name = path.name
            payload = self.write_reports.get(report_name) or self.write_reports.get("default")
            if payload is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
        return kc.CompletedRun(
            args=list(argv), returncode=self.returncode, stdout=self.stdout, stderr=""
        )


@pytest.fixture
def mock_kicad_cli(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingRunner]:
    """Mocks ``find_kicad_cli`` to return a sentinel path and swaps the runner."""
    monkeypatch.setattr(kc, "find_kicad_cli", lambda: "/usr/bin/kicad-cli-mock")
    runner = RecordingRunner()
    prev = kc.set_runner_for_tests(runner)
    try:
        yield runner
    finally:
        kc.set_runner_for_tests(prev)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_kicad_cli_not_found_raises_specific_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force every resolution path to miss: no env override, nothing on PATH,
    # and no stock install location — so the test is deterministic whether or
    # not KiCad happens to be installed on the machine running it.
    monkeypatch.delenv("KICAD_CLI", raising=False)
    monkeypatch.setattr(kc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(kc, "_DEFAULT_CLI_PATHS", ())
    with pytest.raises(kc.KiCadCLINotFoundError):
        kc.find_kicad_cli()


def test_kicad_cli_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KICAD_CLI", "/opt/kicad/bin/kicad-cli")
    assert kc.find_kicad_cli() == "/opt/kicad/bin/kicad-cli"


# ---------------------------------------------------------------------------
# ERC
# ---------------------------------------------------------------------------


def test_run_erc_invokes_correct_args(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    sch = tmp_path / "board.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")
    mock_kicad_cli.write_reports["board.erc.json"] = {"sheets": [{"violations": []}]}

    result = run_erc(sch)

    assert result.ok
    assert result.errors == 0
    call = mock_kicad_cli.calls[0]
    assert call[1:4] == ["sch", "erc", "--severity-all"]
    assert "--format" in call and "json" in call
    assert str(sch) in call


def test_run_erc_parses_errors_and_warnings(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    sch = tmp_path / "board.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")
    mock_kicad_cli.write_reports["board.erc.json"] = {
        "sheets": [{
            "violations": [
                {"severity": "error", "type": "duplicate_pin", "description": "Two pins share pin 1"},
                {"severity": "warning", "type": "unconnected", "description": "U1 pin 3 floats"},
                {"severity": "warning", "type": "unconnected", "description": "U1 pin 5 floats"},
            ]
        }]
    }

    result = run_erc(sch)

    assert result.ok is False
    assert result.errors == 1
    assert result.warnings == 2
    assert len(result.issues) == 3


def test_run_erc_handles_missing_report(
    tmp_path: Path, mock_kicad_cli: RecordingRunner
) -> None:
    """kicad-cli emitting no report → structured cli_failure, not an exception."""
    mock_kicad_cli.stdout = "Failed to load schematic"
    sch = tmp_path / "board.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")
    # No write_reports entry configured → the runner produces no report file.

    result = run_erc(sch)

    assert result.ok is False
    assert result.errors == 1
    assert len(result.issues) == 1
    assert result.issues[0].type == "cli_failure"
    assert "Failed to load schematic" in result.issues[0].description


# ---------------------------------------------------------------------------
# DRC
# ---------------------------------------------------------------------------


def test_run_drc_combines_violation_buckets(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    mock_kicad_cli.write_reports["board.drc.json"] = {
        "violations": [
            {"severity": "error", "type": "clearance", "description": "trace too close"}
        ],
        "unconnected_items": [
            {"severity": "error", "type": "unconnected", "description": "net N$1 has 2 unconnected"}
        ],
        "schematic_parity": [],
    }

    result = run_drc(pcb)

    assert result.ok is False
    assert result.errors == 2
    assert len(result.issues) == 2


def test_run_drc_clean_board(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    pcb = tmp_path / "clean.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    mock_kicad_cli.write_reports["clean.drc.json"] = {
        "violations": [], "unconnected_items": [], "schematic_parity": []
    }

    result = run_drc(pcb)

    assert result.ok
    assert result.errors == 0
    assert result.warnings == 0


def test_run_drc_pre_route_demotes_unconnected(
    tmp_path: Path, mock_kicad_cli: RecordingRunner
) -> None:
    """Pre-route DRC: unrouted ratsnest is a warning; rule violations stay fatal."""
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    mock_kicad_cli.write_reports["board.drc.json"] = {
        "violations": [
            {"severity": "error", "type": "clearance", "description": "trace too close"}
        ],
        "unconnected_items": [
            {"severity": "error", "type": "unconnected", "description": "net N$1 unrouted"},
            {"severity": "error", "type": "unconnected", "description": "net N$2 unrouted"},
        ],
        "schematic_parity": [],
    }

    result = run_drc(pcb, expect_routed=False)

    # The real clearance violation still fails the board...
    assert result.ok is False
    assert result.errors == 1
    # ...but the two unrouted nets are demoted from error to warning.
    assert result.warnings == 2
    assert len(result.issues) == 3


def test_run_drc_handles_missing_report(
    tmp_path: Path, mock_kicad_cli: RecordingRunner
) -> None:
    """kicad-cli emitting no report → structured cli_failure, not an exception."""
    mock_kicad_cli.stdout = "Failed to load board"
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    # No write_reports entry configured → the runner produces no report file.

    result = run_drc(pcb)

    assert result.ok is False
    assert result.errors == 1
    assert len(result.issues) == 1
    assert result.issues[0].type == "cli_failure"
    assert "Failed to load board" in result.issues[0].description


# ---------------------------------------------------------------------------
# Gerber + drill + P&P + STEP
# ---------------------------------------------------------------------------


def test_export_gerbers_uses_default_layers(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    out = tmp_path / "gerbers"

    export_gerbers(pcb, out)

    call = mock_kicad_cli.calls[0]
    assert call[1:4] == ["pcb", "export", "gerbers"]
    layers_idx = call.index("--layers") + 1
    assert "F.Cu" in call[layers_idx] and "Edge.Cuts" in call[layers_idx]


def test_export_drill_emits_excellon(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    out = tmp_path / "drill"

    export_drill(pcb, out)

    call = mock_kicad_cli.calls[0]
    assert call[1:4] == ["pcb", "export", "drill"]
    assert "excellon" in call


def test_export_pos_uses_mm(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")

    export_pick_and_place(pcb, tmp_path / "pos.csv")

    call = mock_kicad_cli.calls[0]
    assert "mm" in call and "csv" in call


def test_export_step_passes_subst_models(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")

    export_step(pcb, tmp_path / "out.step")

    call = mock_kicad_cli.calls[0]
    assert "--subst-models" in call


# ---------------------------------------------------------------------------
# BOM — no kicad-cli needed
# ---------------------------------------------------------------------------


def test_bom_groups_identical_components(tmp_path: Path) -> None:
    board = parse_yaml(Path(__file__).resolve().parents[1] / "examples" / "blinky.yaml")
    rows = build_bom_rows(board)
    # blinky.yaml has 2 unique MPNs → 2 rows
    assert len(rows) == 2
    by_mpn = {r.mpn: r for r in rows}
    assert by_mpn["ESP32-S3-WROOM-1"].quantity == 1
    assert by_mpn["GRM188R71C104KA01D"].quantity == 1


def test_bom_csv_writes_header_and_rows(tmp_path: Path) -> None:
    board = parse_yaml(Path(__file__).resolve().parents[1] / "examples" / "blinky.yaml")
    csv_path = tmp_path / "bom.csv"
    write_bom_csv(board, csv_path)
    text = csv_path.read_text(encoding="utf-8")
    assert text.startswith("Comment,Designator,Footprint,MPN,LCSC,Quantity\n")
    assert "ESP32-S3-WROOM-1" in text


# ---------------------------------------------------------------------------
# Fab package
# ---------------------------------------------------------------------------


def test_fab_package_zip_contains_all_files(tmp_path: Path, mock_kicad_cli: RecordingRunner) -> None:
    # Fake all kicad-cli writes by laying down placeholder files in the output dir.
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    out = tmp_path / "fab"

    # The fab packager calls gerbers, drill, pos. We need to make sure files exist
    # afterwards; the mock runner doesn't create them, so we patch the inner export
    # functions to drop sentinel files.
    import ki_mcp_pcb_core.export.fab_package as fp

    def fake_gerbers(p, o):
        o = Path(o)
        o.mkdir(parents=True, exist_ok=True)
        f = o / "F_Cu.gbr"
        f.write_text("(gerber)")
        return [f]

    def fake_drill(p, o):
        o = Path(o)
        o.mkdir(parents=True, exist_ok=True)
        f = o / "PTH.drl"
        f.write_text("(drl)")
        return [f]

    def fake_pos(p, out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("ref,x,y\n")
        return out_path

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(fp, "export_gerbers", fake_gerbers)
        mp.setattr(fp, "export_drill", fake_drill)
        mp.setattr(fp, "export_pick_and_place", fake_pos)

        board = parse_yaml(Path(__file__).resolve().parents[1] / "examples" / "blinky.yaml")
        pkg = export_fab_package(board, pcb, out)

    assert pkg.zip_path.exists()
    import zipfile
    with zipfile.ZipFile(pkg.zip_path) as zf:
        names = set(zf.namelist())
    assert "F_Cu.gbr" in names
    assert "PTH.drl" in names
    assert any(n.endswith("-pos.csv") for n in names)
    assert any(n.endswith("-bom.csv") for n in names)
