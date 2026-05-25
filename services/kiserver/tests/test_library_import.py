"""Route tests for FR-043 drop-to-import
(`POST /project/{id}/library/import`). Imports into a throwaway tmp
project so nothing on disk is polluted.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiserver.main import app

_SYM = '(kicad_symbol_lib (version 20211014) (symbol "MyPart"))\n'
_MOD = '(footprint "MyFP" (layer "F.Cu"))\n'


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _open_tmp(client: TestClient, tmp_path: Path) -> str:
    (tmp_path / "t.kicad_pro").write_text('{"meta":{"filename":"t.kicad_pro"}}')
    resp = client.post("/project/open", json={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    return resp.json()["project_id"]


def test_import_symbol_writes_file_and_lib_row(client: TestClient, tmp_path: Path) -> None:
    pid = _open_tmp(client, tmp_path)
    resp = client.post(
        f"/project/{pid}/library/import",
        json={"filename": "Custom.kicad_sym", "content": _SYM, "kind": "symbol"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nickname"] == "Custom"
    assert body["lib_id_prefix"] == "Custom:"

    assert (tmp_path / "imported-libs" / "Custom.kicad_sym").read_text() == _SYM
    table = (tmp_path / "sym-lib-table").read_text()
    assert '(name "Custom")' in table
    assert "imported-libs/Custom.kicad_sym" in table


def test_import_footprint_writes_pretty_and_lib_row(client: TestClient, tmp_path: Path) -> None:
    pid = _open_tmp(client, tmp_path)
    resp = client.post(
        f"/project/{pid}/library/import",
        json={"filename": "MyFP.kicad_mod", "content": _MOD, "kind": "footprint"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["nickname"] == "imported"
    assert (tmp_path / "imported.pretty" / "MyFP.kicad_mod").read_text() == _MOD
    assert '(name "imported")' in (tmp_path / "fp-lib-table").read_text()


def test_import_is_idempotent_on_the_lib_row(client: TestClient, tmp_path: Path) -> None:
    pid = _open_tmp(client, tmp_path)
    for _ in range(2):
        client.post(
            f"/project/{pid}/library/import",
            json={"filename": "A.kicad_mod", "content": _MOD, "kind": "footprint"},
        )
    # The .pretty lib row is registered once, not duplicated.
    assert (tmp_path / "fp-lib-table").read_text().count('(name "imported")') == 1


def test_import_rejects_wrong_extension(client: TestClient, tmp_path: Path) -> None:
    pid = _open_tmp(client, tmp_path)
    resp = client.post(
        f"/project/{pid}/library/import",
        json={"filename": "bad.txt", "content": "x", "kind": "symbol"},
    )
    assert resp.status_code == 400


def test_import_strips_path_traversal(client: TestClient, tmp_path: Path) -> None:
    pid = _open_tmp(client, tmp_path)
    resp = client.post(
        f"/project/{pid}/library/import",
        json={"filename": "../../evil.kicad_sym", "content": _SYM, "kind": "symbol"},
    )
    assert resp.status_code == 200
    # The file landed inside the project, not above it.
    assert (tmp_path / "imported-libs" / "evil.kicad_sym").is_file()
    assert not (tmp_path.parent / "evil.kicad_sym").exists()


def test_import_unknown_project_is_404(client: TestClient) -> None:
    resp = client.post(
        "/project/nope/library/import",
        json={"filename": "X.kicad_sym", "content": _SYM, "kind": "symbol"},
    )
    assert resp.status_code == 404
