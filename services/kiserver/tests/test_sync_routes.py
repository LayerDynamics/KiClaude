"""Route-level tests for FR-007 cloud sync (push/pull) on kiserver.

Drives the real FastAPI app + the env-selected (local-FS) object store,
opening the on-disk `esp32_c6_rf` example through `ki_native` and round
-tripping it through `/project/{id}/sync/push` → `/sync/pull`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiserver.main import app

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    # Keep the local object store inside the test's tmp dir; default backend.
    monkeypatch.delenv("KICLAUDE_OBJECT_STORE", raising=False)
    monkeypatch.setenv("KICLAUDE_OBJECT_ROOT", str(tmp_path / "objects"))
    with TestClient(app) as c:
        yield c


def test_sync_push_then_pull_round_trips_the_example(
    client: TestClient, tmp_path: Path
) -> None:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    opened = client.post("/project/open", json={"path": str(example)})
    assert opened.status_code == 200, opened.text
    project_id = opened.json()["project_id"]

    push = client.post(f"/project/{project_id}/sync/push")
    assert push.status_code == 200, push.text
    body = push.json()
    assert body["ok"] is True
    assert body["project_name"] == "esp32_c6_rf"
    assert "esp32_c6_rf.kicad_pcb" in body["files"]
    manifest_key = body["manifest_key"]
    assert len(manifest_key) == 64

    dest = tmp_path / "restored"
    dest.mkdir()
    pull = client.post(
        "/sync/pull", json={"manifest_key": manifest_key, "dest_dir": str(dest)}
    )
    assert pull.status_code == 200, pull.text
    assert "esp32_c6_rf.kicad_pcb" in pull.json()["written"]
    assert (
        (dest / "esp32_c6_rf.kicad_pcb").read_bytes()
        == (example / "esp32_c6_rf.kicad_pcb").read_bytes()
    )


def test_sync_push_unknown_project_is_404(client: TestClient) -> None:
    resp = client.post("/project/does-not-exist/sync/push")
    assert resp.status_code == 404


def test_sync_pull_unknown_manifest_is_404(client: TestClient, tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    resp = client.post(
        "/sync/pull", json={"manifest_key": "a" * 64, "dest_dir": str(dest)}
    )
    assert resp.status_code == 404


def test_sync_pull_missing_dest_is_404(client: TestClient) -> None:
    resp = client.post(
        "/sync/pull",
        json={"manifest_key": "a" * 64, "dest_dir": "/nonexistent/kiclaude/sync/dest"},
    )
    assert resp.status_code == 404
