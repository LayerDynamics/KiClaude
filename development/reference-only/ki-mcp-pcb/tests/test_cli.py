"""CLI integration tests.

We exercise the kimp CLI via Typer's CliRunner — that gives us exit
codes, captured stdout/stderr, and JSON shape assertions without
spawning a subprocess (which would be fragile across Python versions
and CI runners).
"""

from __future__ import annotations

import json
from pathlib import Path

from ki_mcp_pcb_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_version_runs_and_prints_versions() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.stdout
    assert "kimp" in result.stdout
    assert "CIR" in result.stdout


def test_validate_happy_path_exits_zero() -> None:
    result = runner.invoke(app, ["validate", str(EXAMPLES / "blinky.yaml")])
    assert result.exit_code == 0, result.stdout


def test_validate_json_flag_emits_parseable_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(EXAMPLES / "blinky.yaml"), "--json"])
    assert result.exit_code == 0, result.stdout
    # Rich's print_json adds no decoration we'd care about; strip and parse.
    parsed = json.loads(result.stdout)
    assert "issues" in parsed
    assert isinstance(parsed["issues"], list)


def test_validate_unknown_extension_errors(tmp_path: Path) -> None:
    bogus = tmp_path / "thing.xml"
    bogus.write_text("<x/>", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bogus)])
    assert result.exit_code != 0


def test_validate_missing_file_errors() -> None:
    result = runner.invoke(app, ["validate", "/nope/does-not-exist.yaml"])
    assert result.exit_code != 0


def test_validate_returns_nonzero_on_cir_errors(tmp_path: Path) -> None:
    """Hand-construct a YAML with a duplicate refdes — CIR001 should fire."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
cir_version: "0.1"
name: bad
components:
  - refdes: U1
    mpn: A
  - refdes: U1
    mpn: B
nets:
  - name: GND
    net_class: ground
    members: ["U1.1"]
""".strip(),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code != 0
    assert "CIR001" in result.stdout


def test_build_runs_end_to_end_in_sandbox(tmp_path: Path) -> None:
    """`kimp build` exits 0 and writes the project files.

    Environment-agnostic: where kicad-cli is absent the KiCad-gated
    stages skip cleanly; where it's present they run — either way
    synthesis succeeds and the build exits 0.
    """
    result = runner.invoke(app, ["build", str(EXAMPLES / "blinky.yaml"),
                                  "--out", str(tmp_path / "build")])
    assert result.exit_code == 0, result.stdout
    # Files actually landed
    assert (tmp_path / "build" / "blinky-min.kicad_pro").exists()
    assert (tmp_path / "build" / "blinky-min.net").exists()


def test_build_with_unresolved_mpn_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
cir_version: "0.1"
name: bad
components:
  - refdes: U1
    mpn: NONEXISTENT-PART-XYZ
nets:
  - name: GND
    net_class: ground
    members: ["U1.1"]
""".strip(),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["build", str(bad), "--out", str(tmp_path / "out")])
    assert result.exit_code != 0


def test_doctor_runs_and_reports_each_tool() -> None:
    """`kimp doctor` always produces a row per checked tool."""
    result = runner.invoke(app, ["doctor", "--json"])
    # Exit code depends on local env; we only check the structure.
    payload = json.loads(result.stdout)
    names = {entry["name"] for entry in payload}
    assert {"kicad-cli", "kiutils", "freerouting"} <= names
