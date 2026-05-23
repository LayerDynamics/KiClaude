"""Freerouting wrapper tests — subprocess + kicad-cli are mocked."""

from __future__ import annotations

from pathlib import Path

import pytest
from ki_mcp_pcb_core import _kicad_cli as kc
from ki_mcp_pcb_core import routing


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Set up: kicad-cli mocked, freerouting jar fake, java fake."""
    # kicad-cli
    monkeypatch.setattr(kc, "find_kicad_cli", lambda: "/usr/bin/kicad-cli-mock")
    kc_calls = []

    def kc_runner(argv):
        kc_calls.append(list(argv))
        return kc.CompletedRun(args=list(argv), returncode=0, stdout="", stderr="")

    prev_kc = kc.set_runner_for_tests(kc_runner)

    # freerouting + java
    jar = tmp_path / "freerouting.jar"
    jar.write_text("(fake jar)")
    monkeypatch.setenv("FREEROUTING_JAR", str(jar))
    monkeypatch.setenv("JAVA", "/usr/bin/java-mock")

    fr_calls = []

    def fr_runner(argv):
        fr_calls.append(list(argv))
        return routing.RouteRun(args=list(argv), returncode=0, stdout="routed", stderr="")

    prev_fr = routing.set_runner_for_tests(fr_runner)

    try:
        yield kc_calls, fr_calls
    finally:
        kc.set_runner_for_tests(prev_kc)
        routing.set_runner_for_tests(prev_fr)


def test_route_full_pipeline_invokes_kicad_and_freerouting(tmp_path, mock_env) -> None:
    kc_calls, fr_calls = mock_env
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)")

    result = routing.route(pcb)

    # KiCad-cli called twice (export dsn + import ses)
    assert len(kc_calls) == 2
    assert kc_calls[0][1:4] == ["pcb", "export", "dsn"]
    assert kc_calls[1][1:4] == ["pcb", "import", "ses"]

    # Freerouting called once with -de/-do
    assert len(fr_calls) == 1
    args = fr_calls[0]
    assert "-de" in args and "-do" in args
    assert args[0] == "/usr/bin/java-mock"
    assert any(a.endswith("freerouting.jar") for a in args)

    assert result.router == "freerouting"
    assert result.dsn_path.name == "board.dsn"
    assert result.ses_path.name == "board.ses"


def test_route_errors_when_no_freerouting_jar(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FREEROUTING_JAR", raising=False)
    monkeypatch.setattr(kc, "find_kicad_cli", lambda: "/usr/bin/kicad-cli-mock")
    # Restore the kicad-cli runner after the test — leaking the fake
    # would break any later test that shells out to a real kicad-cli.
    prev_runner = kc.set_runner_for_tests(
        lambda argv: kc.CompletedRun(args=list(argv), returncode=0, stdout="", stderr="")
    )
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb)")

    try:
        with pytest.raises(routing.FreeroutingNotFoundError):
            routing.route(pcb)
    finally:
        kc.set_runner_for_tests(prev_runner)


def test_router_choice_other_than_freerouting_raises() -> None:
    with pytest.raises(NotImplementedError):
        routing.route(Path("/nope"), router="kicad_native")
