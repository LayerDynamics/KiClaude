"""Bundled-mirror resolution (T9 / FR-040 / SPEC §9.5).

The empirical gate: a project that pins *no* symbol libraries of its own still
resolves the standard KiCad parts (e.g. `Device:R`) through the pinned bundled
mirror under `libs/`, merged into `GET /project/{id}/library/search`. Plus unit
coverage for the merge/locate helpers (fast, no native indexing).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiserver.library import SearchHit
from kiserver.main import _merge_hits, app, bundled_libs_dir

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLED = REPO_ROOT / "libs"
_HAS_MIRROR = (BUNDLED / "symbols" / "Device.kicad_sym").is_file()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _open_project(client: TestClient, tmp_path: Path, *, with_table: bool) -> str:
    (tmp_path / "t.kicad_pro").write_text('{"meta":{"filename":"t.kicad_pro"}}')
    if with_table:
        # An empty table, exactly like the projects under examples/.
        (tmp_path / "sym-lib-table").write_text("(sym_lib_table (version 7))\n")
    resp = client.post("/project/open", json={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["project_id"])


def _hit(lib_id: str, score: float) -> SearchHit:
    return SearchHit(
        lib_id=lib_id,
        name=lib_id.split(":", 1)[-1],
        library=lib_id.split(":", 1)[0],
        description="",
        footprint_filter="",
        reference="",
        value="",
        footprint="",
        datasheet="",
        mpn="",
        is_power=False,
        score=score,
    )


# --------------------------------------------------------------------------
# Unit coverage for the helpers (no ki_native / no disk).
# --------------------------------------------------------------------------


def test_merge_hits_dedupes_project_over_bundled() -> None:
    project = [_hit("Device:R", 1.0)]
    bundled = [_hit("Device:R", 1.5), _hit("Device:C", 1.2)]
    merged = _merge_hits(project, bundled, limit=10)
    lib_ids = [h.lib_id for h in merged]
    # Device:R appears once and keeps the project's own entry (score 1.0).
    assert lib_ids.count("Device:R") == 1
    assert next(h for h in merged if h.lib_id == "Device:R").score == 1.0
    assert "Device:C" in lib_ids


def test_merge_hits_reranks_and_caps() -> None:
    merged = _merge_hits(
        [_hit("A:1", 0.3)],
        [_hit("B:2", 0.9), _hit("C:3", 0.6)],
        limit=2,
    )
    assert [h.lib_id for h in merged] == ["B:2", "C:3"]


def test_bundled_libs_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "sym-lib-table").write_text("(sym_lib_table (version 7))\n")
    monkeypatch.setenv("KICLAUDE_BUNDLED_LIBS", str(tmp_path))
    assert bundled_libs_dir() == tmp_path


def test_bundled_libs_dir_none_when_env_has_no_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KICLAUDE_BUNDLED_LIBS", str(tmp_path))
    assert bundled_libs_dir() is None


# --------------------------------------------------------------------------
# Integration: real bundled mirror resolves through the route.
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_MIRROR, reason="bundled mirror not populated (run populate_libs.py)")
def test_route_resolves_device_r_from_bundled_mirror(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KICLAUDE_BUNDLED_LIBS", str(BUNDLED))
    pid = _open_project(client, tmp_path, with_table=True)
    resp = client.get(f"/project/{pid}/library/search", params={"query": "R", "limit": 15})
    assert resp.status_code == 200, resp.text
    lib_ids = [h["lib_id"] for h in resp.json()["hits"]]
    assert "Device:R" in lib_ids


@pytest.mark.skipif(not _HAS_MIRROR, reason="bundled mirror not populated (run populate_libs.py)")
def test_route_consults_mirror_even_without_project_table(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A project with no sym-lib-table at all must still see the bundled mirror.
    monkeypatch.setenv("KICLAUDE_BUNDLED_LIBS", str(BUNDLED))
    pid = _open_project(client, tmp_path, with_table=False)
    resp = client.get(
        f"/project/{pid}/library/search",
        params={"query": "USB_C_Receptacle_USB2.0_16P", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    lib_ids = [h["lib_id"] for h in resp.json()["hits"]]
    assert "Connector:USB_C_Receptacle_USB2.0_16P" in lib_ids
