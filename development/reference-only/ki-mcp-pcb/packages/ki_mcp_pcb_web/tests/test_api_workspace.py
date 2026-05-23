"""Integration tests for workspace persistence (G4-T2).

Covers ``session.read/write_persisted_workdir``, ``working_dir_source``,
and the ``GET/POST /api/workspace`` endpoints. The persistence file
location is overridden via ``KIMP_GUI_SESSION_FILE`` so the tests never
touch the real ``~/.config/ki-mcp-pcb/session.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from ki_mcp_pcb_web import session
from ki_mcp_pcb_web.server import app


@pytest.fixture
def isolated_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``KIMP_GUI_SESSION_FILE`` at a per-test path; remove the env override."""
    monkeypatch.delenv(session.WORKDIR_ENV, raising=False)
    persist = tmp_path / "session.json"
    monkeypatch.setenv(session.SESSION_FILE_ENV, str(persist))
    return persist


@pytest.fixture
def client(isolated_session: Path) -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------
# session module — persistence helpers
# --------------------------------------------------------------------------
def test_read_persisted_workdir_returns_none_when_absent(
    isolated_session: Path,
) -> None:
    assert session.read_persisted_workdir() is None


def test_write_then_read_round_trips(
    isolated_session: Path, tmp_path: Path
) -> None:
    target = tmp_path / "my-board"
    target.mkdir()
    session.write_persisted_workdir(target)
    assert session.read_persisted_workdir() == target
    # File written atomically — no leftover .tmp.
    assert not isolated_session.with_suffix(".json.tmp").exists()


def test_read_persisted_workdir_survives_corrupt_json(
    isolated_session: Path,
) -> None:
    isolated_session.parent.mkdir(parents=True, exist_ok=True)
    isolated_session.write_text("not json at all", encoding="utf-8")
    assert session.read_persisted_workdir() is None


def test_read_persisted_workdir_survives_missing_key(
    isolated_session: Path,
) -> None:
    isolated_session.parent.mkdir(parents=True, exist_ok=True)
    isolated_session.write_text(
        json.dumps({"something_else": "x"}), encoding="utf-8"
    )
    assert session.read_persisted_workdir() is None


def test_working_dir_resolution_order(
    isolated_session: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    persisted_dir = tmp_path / "persisted"
    persisted_dir.mkdir()

    # Default — no env, no persisted file.
    assert session.working_dir_source() == "default"

    # Persisted alone — wins over default.
    session.write_persisted_workdir(persisted_dir)
    assert session.working_dir_source() == "persisted"
    assert session.working_dir() == persisted_dir

    # Env override — wins over persisted.
    monkeypatch.setenv(session.WORKDIR_ENV, str(env_dir))
    assert session.working_dir_source() == "env"
    assert session.working_dir() == env_dir


# --------------------------------------------------------------------------
# /api/workspace
# --------------------------------------------------------------------------
def test_get_workspace_reports_the_default_on_a_fresh_install(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    response = client.get("/api/workspace")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "default"
    assert body["path"].endswith("gui-workspace")


def test_post_workspace_persists_and_reflects_through_get(
    client: TestClient, tmp_path: Path
) -> None:
    target = tmp_path / "new-project"
    target.mkdir()

    posted = client.post("/api/workspace", json={"path": str(target)})
    assert posted.status_code == 200
    body = posted.json()
    assert body["source"] == "persisted"
    assert Path(body["path"]) == target

    # GET returns the same.
    got = client.get("/api/workspace").json()
    assert Path(got["path"]) == target
    assert got["source"] == "persisted"


def test_post_workspace_rejects_a_relative_path(client: TestClient) -> None:
    response = client.post("/api/workspace", json={"path": "./relative"})
    assert response.status_code == 400
    assert "absolute" in response.json()["detail"]


def test_post_workspace_rejects_a_nonexistent_path(
    client: TestClient, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist"
    response = client.post("/api/workspace", json={"path": str(missing)})
    assert response.status_code == 400
    assert "existing directory" in response.json()["detail"]


def test_post_workspace_rejects_a_file_target(
    client: TestClient, tmp_path: Path
) -> None:
    a_file = tmp_path / "a.txt"
    a_file.write_text("not a dir")
    response = client.post("/api/workspace", json={"path": str(a_file)})
    assert response.status_code == 400


def test_env_override_wins_in_get(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First persist one path...
    persisted = tmp_path / "persisted"
    persisted.mkdir()
    client.post("/api/workspace", json={"path": str(persisted)})
    # ...then set the env override — that wins.
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    monkeypatch.setenv(session.WORKDIR_ENV, str(env_dir))

    got = client.get("/api/workspace").json()
    assert got["source"] == "env"
    assert Path(got["path"]) == env_dir
