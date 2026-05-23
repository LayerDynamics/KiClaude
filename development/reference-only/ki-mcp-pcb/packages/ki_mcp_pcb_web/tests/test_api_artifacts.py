"""Integration tests for the build-artifact API — listing, download, and the
path-traversal guard on ``/api/artifacts/{path}``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import HTTPException
from fastapi.testclient import TestClient
from ki_mcp_pcb_web import server, session
from ki_mcp_pcb_web.server import app

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
BLINKY = (EXAMPLES / "blinky.yaml").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "ki_mcp_pcb_core._kicad_cli.is_available", lambda: False
    )
    return TestClient(app)


def test_empty_build_dir_lists_no_artifacts(client: TestClient) -> None:
    assert client.get("/api/artifacts").json() == []


def test_lists_and_downloads_artifacts(client: TestClient) -> None:
    build = session.build_dir()
    (build / "board.kicad_pcb").write_text("(kicad_pcb)", encoding="utf-8")
    (build / "fab").mkdir()
    (build / "fab" / "out.zip").write_bytes(b"PK\x03\x04zipdata")

    listing = client.get("/api/artifacts").json()
    by_path = {a["path"]: a for a in listing}
    assert "board.kicad_pcb" in by_path
    assert "fab/out.zip" in by_path  # nested files use their relative path
    assert by_path["fab/out.zip"]["name"] == "out.zip"
    assert by_path["fab/out.zip"]["size"] == len(b"PK\x03\x04zipdata")

    download = client.get("/api/artifacts/fab/out.zip")
    assert download.status_code == 200
    assert download.content == b"PK\x03\x04zipdata"


def test_download_of_a_missing_artifact_is_404(client: TestClient) -> None:
    assert client.get("/api/artifacts/nope.kicad_pcb").status_code == 404


def test_path_traversal_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path escaping the build directory must not be served (NFR-7).

    The guard is tested directly — HTTP clients normalize ``..`` out of the
    URL before it reaches the route, so this exercises the real check.
    """
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    # A secret sitting in the working dir, one level above build/.
    (session.working_dir() / "secret.txt").write_text("private", encoding="utf-8")

    for escaping in ("../secret.txt", "../../../../etc/passwd"):
        with pytest.raises(HTTPException) as exc_info:
            server.get_artifact(escaping)
        assert exc_info.value.status_code == 404


def test_artifacts_appear_after_a_build(client: TestClient) -> None:
    client.put("/api/cir", json={"text": BLINKY})
    client.post("/api/build", json={"run_route": False})

    paths = {a["path"] for a in client.get("/api/artifacts").json()}
    assert any(p.endswith(".kicad_pcb") for p in paths)
    assert any(p.endswith(".net") for p in paths)
