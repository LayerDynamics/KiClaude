"""Populator wrapper tests — subprocess is mocked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ki_mcp_pcb_core.synthesis import populator as P


@pytest.fixture
def fake_runner(monkeypatch):
    """Replace the subprocess runner with a deterministic fake."""
    calls: list[list[str]] = []
    config: dict = {"returncode": 0, "report": None, "stderr": ""}

    def runner(argv):
        calls.append(list(argv))
        # When the populator script is invoked, optionally write a report
        if "--report" in argv:
            idx = list(argv).index("--report")
            report_path = Path(argv[idx + 1])
            if config["report"] is not None:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(config["report"]), encoding="utf-8")
        # When the python "can you import pcbnew" probe is invoked
        if "-c" in argv and "import pcbnew" in (argv[-1] if argv else ""):
            return P._PopRun(args=list(argv),
                             returncode=config.get("probe_rc", 0),
                             stdout="9.0.1", stderr="")
        return P._PopRun(args=list(argv), returncode=config["returncode"],
                         stdout="", stderr=config["stderr"])

    prev = P.set_runner_for_tests(runner)
    try:
        yield calls, config
    finally:
        P.set_runner_for_tests(prev)


def test_populator_reports_pcbnew_unavailable(monkeypatch, tmp_path, fake_runner) -> None:
    calls, config = fake_runner
    config["probe_rc"] = 1  # every candidate fails

    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    net = tmp_path / "board.net"
    net.write_text("(export)")

    result = P.populate(pcb, net)

    assert result.status == "pcbnew_unavailable"
    assert not result.ok
    # No populator script invocation when pcbnew probe fails
    assert not any("kicad_populate.py" in " ".join(c) for c in calls)


def test_populator_invokes_script_with_correct_args(tmp_path, fake_runner) -> None:
    calls, config = fake_runner
    config["report"] = {
        "ok": True, "pcb_path": str(tmp_path / "board.kicad_pcb"),
        "netlist_path": str(tmp_path / "board.net"),
        "components_placed": 3, "footprints_missing": [], "errors": [],
    }

    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    net = tmp_path / "board.net"
    net.write_text("(export)")

    result = P.populate(pcb, net)

    assert result.ok
    assert result.components_placed == 3
    # One probe call + one script call
    script_calls = [c for c in calls if "kicad_populate.py" in " ".join(c)]
    assert len(script_calls) == 1
    args = script_calls[0]
    assert "--pcb" in args and str(pcb) in args
    assert "--net" in args and str(net) in args
    assert "--placement" in args and "grid" in args


def test_populator_status_maps_returncodes(tmp_path, fake_runner) -> None:
    _calls, config = fake_runner
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    net = tmp_path / "board.net"
    net.write_text("(export)")

    config["returncode"] = 3
    config["report"] = {"errors": ["netlist parse boom"]}
    r = P.populate(pcb, net)
    assert r.status == "netlist_error"
    assert "netlist parse boom" in r.errors

    config["returncode"] = 5
    config["report"] = {"footprints_missing": ["My:Fp"]}
    r = P.populate(pcb, net)
    assert r.status == "footprint_missing"
    assert "My:Fp" in r.footprints_missing


def test_populator_report_path_explicit(tmp_path, fake_runner) -> None:
    calls, config = fake_runner
    config["report"] = {"ok": True, "components_placed": 1}
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    net = tmp_path / "board.net"
    net.write_text("(export)")
    explicit = tmp_path / "reports" / "out.json"

    result = P.populate(pcb, net, report_path=explicit)

    assert result.ok
    # The script was told the explicit report path
    script_calls = [c for c in calls if "kicad_populate.py" in " ".join(c)]
    assert any(str(explicit) in args for args in script_calls)


def test_populator_threads_placements_into_design_sidecar(tmp_path, fake_runner) -> None:
    """plan_placement coordinates reach the populate script via the sidecar."""
    from ki_mcp_pcb_core.placement import Placement

    calls, config = fake_runner
    config["report"] = {"ok": True, "components_placed": 2}
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    net = tmp_path / "board.net"
    net.write_text("(export)")

    P.populate(pcb, net, placements=[
        Placement(refdes="U1", x_mm=12.5, y_mm=34.0),
        Placement(refdes="C1", x_mm=40.0, y_mm=8.0),
    ])

    sidecar = pcb.with_suffix(".design.json")
    assert sidecar.exists()
    design = json.loads(sidecar.read_text(encoding="utf-8"))
    assert design["placements"] == {"U1": [12.5, 34.0], "C1": [40.0, 8.0]}
    # The script was pointed at the sidecar.
    script_calls = [c for c in calls if "kicad_populate.py" in " ".join(c)]
    assert any("--design-json" in args for args in script_calls)


def test_pcbnew_available_helper(fake_runner) -> None:
    _calls, config = fake_runner
    config["probe_rc"] = 0
    assert P.pcbnew_available() is True

    config["probe_rc"] = 1
    assert P.pcbnew_available() is False
