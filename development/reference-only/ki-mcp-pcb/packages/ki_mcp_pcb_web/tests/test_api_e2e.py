"""End-to-end smoke test for the milestone-G1 GUI loop.

Drives the full backend sequence a user would walk through in the
browser — open the (empty) working CIR, save one, read it back, run the
pipeline, and download the artifacts — through FastAPI's ``TestClient``.
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
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    # Exercise the API contract, not KiCad — gated stages skip cleanly.
    monkeypatch.setattr(
        "ki_mcp_pcb_core._kicad_cli.is_available", lambda: False
    )
    return TestClient(app)


def test_g1_loop_from_empty_cir_to_downloadable_artifacts(
    client: TestClient,
) -> None:
    # 1. A fresh session has no working CIR.
    assert client.get("/api/cir").json()["exists"] is False

    # 2. Save a CIR — it parses and validates clean.
    saved = client.put("/api/cir", json={"text": BLINKY})
    assert saved.status_code == 200
    assert saved.json()["validation"]["ok"] is True

    # 3. Read it back unchanged.
    reloaded = client.get("/api/cir").json()
    assert reloaded["exists"] is True
    assert reloaded["text"] == BLINKY

    # 4. Run the pipeline — synthesis succeeds, KiCad-gated stages skip.
    build = client.post("/api/build", json={"run_route": False})
    assert build.status_code == 200
    result = build.json()
    assert result["ok"] is True
    stage_names = {s["name"] for s in result["stages"]}
    assert {"parse", "validate", "sourcing", "synthesize"} <= stage_names

    # 5. The build's artifacts are listed and downloadable.
    artifacts = client.get("/api/artifacts").json()
    paths = {a["path"] for a in artifacts}
    assert any(p.endswith(".kicad_pcb") for p in paths)
    assert any(p.endswith(".net") for p in paths)

    pcb_path = next(p for p in paths if p.endswith(".kicad_pcb"))
    download = client.get(f"/api/artifacts/{pcb_path}")
    assert download.status_code == 200
    assert download.content  # the file has bytes


def test_g1_loop_streams_the_build(client: TestClient) -> None:
    """The same loop, but the build is consumed over the SSE stream."""
    client.put("/api/cir", json={"text": BLINKY})

    response = client.get("/api/build/stream")
    assert response.status_code == 200
    kinds = [
        line[len("event: ") :]
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert kinds.count("stage") >= 4
    assert kinds[-1] == "done"
