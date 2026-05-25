"""Route test for the kc_mpn_resolve library-candidate backing
(`GET /project/{id}/library/search`). The example pins no symbol
libraries, so it returns an empty (but valid) hit list — the route
must degrade gracefully rather than error.
"""

from __future__ import annotations

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


def test_library_search_empty_table_returns_no_hits(client: TestClient) -> None:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    opened = client.post("/project/open", json={"path": str(example)})
    assert opened.status_code == 200, opened.text
    project_id = opened.json()["project_id"]

    resp = client.get(f"/project/{project_id}/library/search", params={"query": "STM32"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["query"] == "STM32"
    assert body["hits"] == []  # esp32_c6_rf's sym-lib-table is empty


def test_library_search_blank_query_returns_no_hits(client: TestClient) -> None:
    example = _REPO_ROOT / "examples" / "esp32_c6_rf"
    project_id = client.post("/project/open", json={"path": str(example)}).json()["project_id"]
    resp = client.get(f"/project/{project_id}/library/search", params={"query": "  "})
    assert resp.status_code == 200
    assert resp.json()["hits"] == []


def test_library_search_unknown_project_is_404(client: TestClient) -> None:
    resp = client.get("/project/nope/library/search", params={"query": "x"})
    assert resp.status_code == 404
