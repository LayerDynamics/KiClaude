"""Route test for kc_session_fork's kiserver backing
(`POST /project/{id}/session/fork`). Verifies it writes an
agent-readable session manifest recording `forked_from`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiserver.main import app

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _open_example(client: TestClient) -> tuple[str, Path]:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    resp = client.post("/project/open", json={"path": str(example)})
    assert resp.status_code == 200, resp.text
    return resp.json()["project_id"], example


def test_session_fork_writes_manifest(client: TestClient) -> None:
    project_id, example = _open_example(client)
    sessions_dir = example / ".kiclaude" / "sessions"
    try:
        resp = client.post(
            f"/project/{project_id}/session/fork",
            json={"parent_session_id": "parent-abc", "label": "what-if"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        new_id = body["new_session_id"]
        assert body["forked_from"] == "parent-abc"

        manifest_path = sessions_dir / f"{new_id}.json"
        assert manifest_path.is_file()
        data = json.loads(manifest_path.read_text())
        assert data["session_id"] == new_id
        assert data["forked_from"] == "parent-abc"
        assert data["label"] == "what-if"
        assert data["project_id"] == project_id
        # Agent-compatible shape (SessionManifest fields present).
        assert {"project_path", "started_at_unix", "schema_version"} <= set(data)
    finally:
        # Clean the manifest the test wrote into the on-disk example.
        if sessions_dir.exists():
            for f in sessions_dir.glob("*.json"):
                f.unlink()
            try:
                sessions_dir.rmdir()
                (example / ".kiclaude").rmdir()
            except OSError:
                pass


def test_session_fork_unknown_project_is_404(client: TestClient) -> None:
    resp = client.post(
        "/project/does-not-exist/session/fork",
        json={"parent_session_id": "x"},
    )
    assert resp.status_code == 404
