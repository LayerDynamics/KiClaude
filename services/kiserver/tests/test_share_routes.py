"""Route-level tests for FR-080 read-only share links.

Opens the on-disk `esp32_c6_rf` example, freezes it into a
content-addressed share, and verifies the read-only resolve + per-file
fetch surface. Uses the default (local-FS) object store rooted in the
test's tmp dir.
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
    monkeypatch.delenv("KICLAUDE_OBJECT_STORE", raising=False)
    monkeypatch.setenv("KICLAUDE_OBJECT_ROOT", str(tmp_path / "objects"))
    with TestClient(app) as c:
        yield c


def _share_example(client: TestClient) -> str:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    opened = client.post("/project/open", json={"path": str(example)})
    assert opened.status_code == 200, opened.text
    project_id = opened.json()["project_id"]
    created = client.post(f"/project/{project_id}/share")
    assert created.status_code == 200, created.text
    return str(created.json()["token"])


def test_create_share_returns_content_addressed_token(client: TestClient) -> None:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    opened = client.post("/project/open", json={"path": str(example)})
    project_id = opened.json()["project_id"]
    created = client.post(f"/project/{project_id}/share")
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["ok"] is True
    assert len(body["token"]) == 64
    assert body["url"] == f"/share/{body['token']}"
    assert body["project_name"] == "esp32_c6_rf"
    assert "esp32_c6_rf.kicad_pcb" in body["files"]


def test_resolve_share_is_read_only_metadata(client: TestClient) -> None:
    token = _share_example(client)
    resp = client.get(f"/share/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["read_only"] is True
    assert body["project_name"] == "esp32_c6_rf"
    assert "esp32_c6_rf.kicad_sch" in body["files"]


def test_share_file_returns_exact_bytes(client: TestClient) -> None:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    token = _share_example(client)
    resp = client.get(f"/share/{token}/file", params={"path": "esp32_c6_rf.kicad_pcb"})
    assert resp.status_code == 200, resp.text
    assert resp.content == (example / "esp32_c6_rf.kicad_pcb").read_bytes()


def test_resolve_unknown_token_is_404(client: TestClient) -> None:
    resp = client.get(f"/share/{'a' * 64}")
    assert resp.status_code == 404


def test_share_file_unknown_path_is_404(client: TestClient) -> None:
    token = _share_example(client)
    resp = client.get(f"/share/{token}/file", params={"path": "not-in-share.kicad_pcb"})
    assert resp.status_code == 404


def test_create_share_unknown_project_is_404(client: TestClient) -> None:
    resp = client.post("/project/does-not-exist/share")
    assert resp.status_code == 404
