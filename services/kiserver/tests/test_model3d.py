"""3D model resolver (T10 / FR-029 / D6).

Covers `GET /project/{id}/model3d`: the env-var + `.wrl`→`.step` resolution
helper, and the route serving real STEP bytes from the bundled mirror with
path-traversal protection.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiserver.main import _resolve_model_relpath, app

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLED = REPO_ROOT / "libs"
_STEP_REL = "packages3D/Capacitor_SMD.3dshapes/C_0402_1005Metric.step"
_HAS_MODEL = (BUNDLED / _STEP_REL).is_file()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _open_project(client: TestClient, tmp_path: Path) -> str:
    (tmp_path / "t.kicad_pro").write_text('{"meta":{"filename":"t.kicad_pro"}}')
    resp = client.post("/project/open", json={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["project_id"])


# --------------------------------------------------------------------------
# Path resolution helper (pure).
# --------------------------------------------------------------------------


def test_resolve_swaps_wrl_for_step_under_3dmodel_dir() -> None:
    assert (
        _resolve_model_relpath("${KICAD9_3DMODEL_DIR}/Capacitor_SMD.3dshapes/C_0402_1005Metric.wrl")
        == "Capacitor_SMD.3dshapes/C_0402_1005Metric.step"
    )


def test_resolve_handles_kicad7_and_8_prefixes() -> None:
    assert _resolve_model_relpath("${KICAD7_3DMODEL_DIR}/RF_Module.3dshapes/X.wrl") == (
        "RF_Module.3dshapes/X.step"
    )
    assert _resolve_model_relpath("${KICAD8_3DMODEL_DIR}/RF_Module.3dshapes/X.step") == (
        "RF_Module.3dshapes/X.step"
    )


def test_resolve_strips_bundled_libs_packages3d_prefix() -> None:
    assert _resolve_model_relpath("${KICLAUDE_BUNDLED_LIBS}/packages3D/RF.3dshapes/X.wrz") == (
        "RF.3dshapes/X.step"
    )


def test_resolve_rejects_absolute_paths() -> None:
    assert _resolve_model_relpath("/etc/passwd") is None
    assert _resolve_model_relpath("") is None


# --------------------------------------------------------------------------
# Route.
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_MODEL, reason="STEP model not in bundled mirror")
def test_model3d_serves_step_sibling_of_wrl_ref(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KICLAUDE_BUNDLED_LIBS", str(BUNDLED))
    pid = _open_project(client, tmp_path)
    resp = client.get(
        f"/project/{pid}/model3d",
        params={"path": "${KICAD9_3DMODEL_DIR}/Capacitor_SMD.3dshapes/C_0402_1005Metric.wrl"},
    )
    assert resp.status_code == 200, resp.text
    # The route returned the .step sibling's exact bytes.
    assert resp.content == (BUNDLED / _STEP_REL).read_bytes()
    assert resp.content.startswith(b"ISO-10303-21;")


@pytest.mark.skipif(not _HAS_MODEL, reason="STEP model not in bundled mirror")
def test_model3d_blocks_path_traversal(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KICLAUDE_BUNDLED_LIBS", str(BUNDLED))
    pid = _open_project(client, tmp_path)
    resp = client.get(
        f"/project/{pid}/model3d",
        params={"path": "${KICAD9_3DMODEL_DIR}/../../../../../../etc/passwd"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.skipif(not _HAS_MODEL, reason="STEP model not in bundled mirror")
def test_model3d_missing_model_is_404(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KICLAUDE_BUNDLED_LIBS", str(BUNDLED))
    pid = _open_project(client, tmp_path)
    resp = client.get(
        f"/project/{pid}/model3d",
        params={"path": "${KICAD9_3DMODEL_DIR}/RF_Module.3dshapes/Does_Not_Exist.wrl"},
    )
    assert resp.status_code == 404, resp.text


def test_model3d_unknown_project_is_404(client: TestClient) -> None:
    resp = client.get("/project/nope/model3d", params={"path": "x.step"})
    assert resp.status_code == 404
