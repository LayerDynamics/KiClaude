"""Integration tests for the kiserver FastAPI app."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kiserver import __version__
from kiserver.main import app
from kiserver.project import REGISTRY

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@pytest.fixture()
def client() -> TestClient:
    REGISTRY.clear()
    return TestClient(app)


@pytest.fixture()
def repo_root() -> Path:
    """The kiclaude repo root — tests use it to resolve `examples/blinky`."""
    return Path(__file__).resolve().parents[3]


def test_health_envelope(client: TestClient) -> None:
    """`GET /health` returns the standard envelope with a `native` flag."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "kiserver"
    assert body["version"] == __version__
    assert isinstance(body["native"], bool)


def test_open_blinky_round_trips_through_ki_native(client: TestClient, repo_root: Path) -> None:
    """The acceptance gate for M0-P-04: `POST /project/open` on
    `examples/blinky/` returns `{ok:true, project_id:<uuid4>,
    summary.name:"blinky"}`."""
    blinky = repo_root / "examples" / "blinky"
    resp = client.post("/project/open", json={"path": str(blinky)})
    if resp.status_code == 503:
        pytest.skip("ki_native not installed in this venv")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert UUID4_RE.match(body["project_id"]), body["project_id"]
    assert body["summary"]["name"] == "blinky"
    assert body["summary"]["layer_count"] == 3
    assert body["summary"]["footprint_count"] == 2
    assert body["summary"]["net_count"] == 1


def test_open_nonexistent_path_returns_404(client: TestClient) -> None:
    resp = client.post("/project/open", json={"path": "/nonexistent/m0p04/test"})
    assert resp.status_code in (404, 503)  # 503 if ki_native missing — that's the prior guard


def test_open_not_a_directory_returns_400(client: TestClient, repo_root: Path) -> None:
    """A file (not a directory) is rejected with 400 — kiclaude opens
    project *directories*, not individual files."""
    file_path = repo_root / "examples" / "blinky" / "blinky.kicad_pro"
    resp = client.post("/project/open", json={"path": str(file_path)})
    assert resp.status_code in (400, 503)


def test_get_unknown_project_id_returns_404(client: TestClient) -> None:
    resp = client.get("/project/00000000-0000-4000-8000-000000000000")
    assert resp.status_code == 404


def test_get_returns_full_project(client: TestClient, repo_root: Path) -> None:
    """After opening, `GET /project/{id}` returns the full KCIR project."""
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]
    resp = client.get(f"/project/{project_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == project_id
    assert body["project"]["name"] == "blinky"
    assert "pcb" in body["project"]


def test_open_view_selection_returns_only_requested_slices(
    client: TestClient, repo_root: Path
) -> None:
    """`POST /project/open` with `view=["pcb"]` returns the PCB
    slice + project_id but omits the schematic + metadata."""
    blinky = repo_root / "examples" / "blinky"
    resp = client.post(
        "/project/open", json={"path": str(blinky), "view": ["pcb", "metadata"]}
    )
    if resp.status_code == 503:
        pytest.skip("ki_native not installed")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "pcb" in body
    assert "metadata" in body
    assert "schematic" not in body
    assert "summary" not in body  # not requested
    assert "project" not in body


def test_save_round_trips_into_target_directory(
    client: TestClient, repo_root: Path, tmp_path: Path
) -> None:
    """`POST /project/{id}/save` writes both .kicad_pcb and
    .kicad_sch back to disk under the canonical stem name."""
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]
    save_resp = client.post(
        f"/project/{project_id}/save",
        json={"target_dir": str(tmp_path)},
    )
    assert save_resp.status_code == 200, save_resp.text
    body = save_resp.json()
    assert body["ok"] is True
    assert body["project_id"] == project_id
    written = body["written"]
    assert any(p.endswith(".kicad_pcb") for p in written), written
    pcb_text = (tmp_path / "blinky.kicad_pcb").read_text()
    assert "(kicad_pcb" in pcb_text


def test_save_unknown_project_returns_404(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/project/00000000-0000-4000-8000-000000000000/save",
        json={"target_dir": str(tmp_path)},
    )
    assert resp.status_code == 404


def test_save_missing_target_dir_returns_404(
    client: TestClient, repo_root: Path
) -> None:
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]
    resp = client.post(
        f"/project/{project_id}/save",
        json={"target_dir": "/nonexistent/m1p01/target"},
    )
    assert resp.status_code == 404


def test_save_emits_otel_span(
    client: TestClient, repo_root: Path, tmp_path: Path
) -> None:
    """The save endpoint emits a `kiserver.project.save` span with
    `project_id`, `target_dir`, and `files_written` attributes."""
    from kiserver.telemetry import reset_for_tests

    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]

    exporter = reset_for_tests()
    save_resp = client.post(
        f"/project/{project_id}/save",
        json={"target_dir": str(tmp_path)},
    )
    assert save_resp.status_code == 200, save_resp.text
    spans = exporter.get_finished_spans()
    matching = [s for s in spans if s.name == "kiserver.project.save"]
    assert matching, f"no save span found among {[s.name for s in spans]}"
    attrs = matching[-1].attributes or {}
    assert attrs.get("project_id") == project_id
    assert attrs.get("target_dir") == str(tmp_path.resolve())
    assert attrs.get("files_written", 0) >= 1


def test_save_is_idempotent(
    client: TestClient, repo_root: Path, tmp_path: Path
) -> None:
    """Calling save twice in a row produces byte-identical files."""
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]

    client.post(f"/project/{project_id}/save", json={"target_dir": str(tmp_path)})
    first = (tmp_path / "blinky.kicad_pcb").read_bytes()
    client.post(f"/project/{project_id}/save", json={"target_dir": str(tmp_path)})
    second = (tmp_path / "blinky.kicad_pcb").read_bytes()
    assert first == second


# ---------------------------------------------------------------------
# M1-T-08 snapshot routes.
# ---------------------------------------------------------------------


def test_snapshot_create_then_revert_round_trip(
    client: TestClient, repo_root: Path
) -> None:
    """`POST /project/{id}/snapshot/create` records the current KCIR;
    after mutating the project via `/replace`, `/snapshot/revert`
    restores it."""
    from kc_mcp.tools.snapshot import _clear_for_tests

    _clear_for_tests()
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]
    original = client.get(f"/project/{project_id}").json()["project"]

    snap_resp = client.post(
        f"/project/{project_id}/snapshot/create", json={"label": "baseline"}
    )
    assert snap_resp.status_code == 200, snap_resp.text
    snap_body = snap_resp.json()
    assert snap_body["ok"] is True
    snapshot_id = snap_body["snapshot_id"]

    # Mutate by swapping in a project dict with the schematic emptied
    # (preserve all other keys).
    mutated = {**original, "schematic": {**original["schematic"], "labels": [{"x": 1}]}}
    replace_resp = client.post(
        f"/project/{project_id}/replace", json={"project": mutated}
    )
    assert replace_resp.status_code == 200, replace_resp.text
    after_mut = client.get(f"/project/{project_id}").json()["project"]
    assert after_mut["schematic"]["labels"] == [{"x": 1}]

    revert_resp = client.post(
        f"/project/{project_id}/snapshot/revert",
        json={"snapshot_id": snapshot_id},
    )
    assert revert_resp.status_code == 200, revert_resp.text
    body = revert_resp.json()
    assert body["ok"] is True
    assert body["reverted_to_label"] == "baseline"

    restored = client.get(f"/project/{project_id}").json()["project"]
    assert restored["schematic"]["labels"] == original["schematic"]["labels"]


def test_snapshot_revert_unknown_snapshot_returns_404(
    client: TestClient, repo_root: Path
) -> None:
    from kc_mcp.tools.snapshot import _clear_for_tests

    _clear_for_tests()
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]
    resp = client.post(
        f"/project/{project_id}/snapshot/revert",
        json={"snapshot_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 404


def test_snapshot_create_unknown_project_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/project/00000000-0000-4000-8000-000000000000/snapshot/create",
        json={"label": "x"},
    )
    assert resp.status_code == 404


def test_snapshots_listing(client: TestClient, repo_root: Path) -> None:
    """`GET /project/{id}/snapshots` lists labels + timestamps."""
    from kc_mcp.tools.snapshot import _clear_for_tests

    _clear_for_tests()
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]
    client.post(
        f"/project/{project_id}/snapshot/create", json={"label": "first"}
    )
    client.post(
        f"/project/{project_id}/snapshot/create", json={"label": "second"}
    )
    resp = client.get(f"/project/{project_id}/snapshots")
    assert resp.status_code == 200
    snaps = resp.json()["snapshots"]
    assert {s["label"] for s in snaps} == {"first", "second"}
    assert all("snapshot_id" in s and "ts" in s for s in snaps)


def test_snapshot_revert_emits_otel_span(
    client: TestClient, repo_root: Path
) -> None:
    from kc_mcp.tools.snapshot import _clear_for_tests
    from kiserver.telemetry import reset_for_tests

    _clear_for_tests()
    blinky = repo_root / "examples" / "blinky"
    open_resp = client.post("/project/open", json={"path": str(blinky)})
    if open_resp.status_code == 503:
        pytest.skip("ki_native not installed")
    project_id = open_resp.json()["project_id"]
    snap_resp = client.post(
        f"/project/{project_id}/snapshot/create", json={"label": "L"}
    )
    snapshot_id = snap_resp.json()["snapshot_id"]
    exporter = reset_for_tests()
    revert_resp = client.post(
        f"/project/{project_id}/snapshot/revert",
        json={"snapshot_id": snapshot_id},
    )
    assert revert_resp.status_code == 200, revert_resp.text
    matching = [
        s
        for s in exporter.get_finished_spans()
        if s.name == "kiserver.project.snapshot_revert"
    ]
    assert matching, "no snapshot_revert span found"
    attrs = matching[-1].attributes or {}
    assert attrs.get("project_id") == project_id
    assert attrs.get("snapshot_id") == snapshot_id
    assert attrs.get("reverted_to_label") == "L"
