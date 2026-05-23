"""Integration tests for the pipeline API — ``/api/doctor``, ``/api/build``,
and the SSE ``/api/build/stream``.

These exercise the API contract, not KiCad: the ``client`` fixture makes
kicad-cli look absent so the KiCad-gated stages skip cleanly (the full
real-KiCad build is covered by ``tests/test_end_to_end.py``). Each test
runs in an isolated working directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from ki_mcp_pcb_web import session
from ki_mcp_pcb_web.server import app

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
BLINKY = (EXAMPLES / "blinky.yaml").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient with an isolated workdir and kicad-cli forced absent."""
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "ki_mcp_pcb_core._kicad_cli.is_available", lambda: False
    )
    return TestClient(app)


def _parse_sse(body: str) -> list[tuple[str, str]]:
    """Parse an SSE body into a list of (event, data) pairs."""
    events: list[tuple[str, str]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        events.append((event, data))
    return events


def test_doctor_returns_environment_checks(client: TestClient) -> None:
    response = client.get("/api/doctor")
    assert response.status_code == 200
    checks = response.json()
    assert isinstance(checks, list) and checks
    for check in checks:
        assert {"name", "ok", "detail"} <= check.keys()


def test_build_without_a_working_cir_returns_400(client: TestClient) -> None:
    response = client.post("/api/build", json={"run_route": False})
    assert response.status_code == 400


def test_build_runs_the_pipeline_and_reports_stages(client: TestClient) -> None:
    client.put("/api/cir", json={"text": BLINKY})

    response = client.post("/api/build", json={"run_route": False})
    assert response.status_code == 200
    body = response.json()
    stage_names = {s["name"] for s in body["stages"]}
    assert {"parse", "validate", "sourcing", "synthesize"} <= stage_names
    assert body["ok"] is True  # synthesis succeeds; gated stages skip cleanly


def test_build_skips_kicad_stages_when_cli_absent(client: TestClient) -> None:
    client.put("/api/cir", json={"text": BLINKY})

    body = client.post("/api/build", json={"run_route": False}).json()
    by_name = {s["name"]: s for s in body["stages"]}
    for gated in ("erc", "drc", "fab"):
        assert by_name[gated]["detail"].get("skipped") is True


def test_build_stream_emits_a_stage_event_per_stage_then_done(
    client: TestClient,
) -> None:
    client.put("/api/cir", json={"text": BLINKY})

    response = client.get("/api/build/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    kinds = [event for event, _ in events]
    assert kinds.count("stage") >= 4  # at least parse/validate/sourcing/synthesize
    assert kinds[-1] == "done"
    assert "stage" in kinds
