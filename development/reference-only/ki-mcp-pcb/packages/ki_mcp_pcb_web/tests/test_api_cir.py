"""Integration tests for the working-CIR API (``GET``/``PUT /api/cir``).

Exercises the round-trip through FastAPI's ``TestClient``: each test gets
an isolated working directory via the ``KIMP_GUI_WORKDIR`` override.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from ki_mcp_pcb_web import session
from ki_mcp_pcb_web.server import app

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"

# A CIR that parses cleanly but fails validation — net GND references the
# undefined component U99 (validator CIR002).
_INVALID_CIR = textwrap.dedent(
    """
    cir_version: "0.2"
    name: invalid-demo
    components:
      - refdes: U1
        mpn: ESP32-S3-WROOM-1
    nets:
      - name: GND
        net_class: ground
        members: ["U1.1", "U99.1"]
    """
).strip()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient whose working directory is an isolated tmp dir."""
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    return TestClient(app)


def test_get_cir_on_fresh_workdir_reports_absent(client: TestClient) -> None:
    response = client.get("/api/cir")
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert body["text"] == ""
    assert body["board"] is None


def test_put_then_get_round_trips_a_valid_cir(client: TestClient) -> None:
    cir_text = (EXAMPLES / "blinky.yaml").read_text(encoding="utf-8")

    put = client.put("/api/cir", json={"text": cir_text})
    assert put.status_code == 200
    put_body = put.json()
    assert put_body["exists"] is True
    assert put_body["parse_error"] is None
    assert put_body["validation"]["ok"] is True
    assert put_body["board"]["name"] == "blinky-min"

    got = client.get("/api/cir")
    assert got.status_code == 200
    assert got.json()["text"] == cir_text
    assert got.json()["exists"] is True


def test_put_invalid_yaml_is_rejected_and_not_written(client: TestClient) -> None:
    bad = client.put("/api/cir", json={"text": "[unterminated flow sequence"})
    assert bad.status_code == 400
    assert "parse error" in bad.json()["detail"]

    # The broken text must not have been written — the file stays absent.
    assert client.get("/api/cir").json()["exists"] is False


def test_put_cir_with_validation_errors_returns_ok_false(client: TestClient) -> None:
    response = client.put("/api/cir", json={"text": _INVALID_CIR})
    # It parses, so the write succeeds (200) ...
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["parse_error"] is None
    # ... but validation reports the dangling net reference.
    assert body["validation"]["ok"] is False
    assert body["validation"]["errors"]


# --------------------------------------------------------------------------
# PUT /api/cir/board — structured Board JSON path (SPEC-1 G3-T1)
# --------------------------------------------------------------------------
def test_put_cir_board_round_trips_a_real_example(client: TestClient) -> None:
    from ki_mcp_pcb_core.parsers.yaml import parse_yaml

    source = parse_yaml((EXAMPLES / "blinky.yaml").read_text(encoding="utf-8"))
    payload = source.model_dump(mode="json")

    response = client.put("/api/cir/board", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["parse_error"] is None
    # The structured write must yield an identical Board after re-parsing
    # the on-disk canonical YAML — the round-trip the form editor relies on.
    written = parse_yaml(body["text"])
    assert written == source
    # And the CirState shape carries the parsed board/validation/bom.
    assert body["validation"] is not None
    assert body["board"]["name"] == source.name


def test_put_cir_board_get_returns_the_canonical_yaml(client: TestClient) -> None:
    from ki_mcp_pcb_core.parsers.yaml import parse_yaml

    source = parse_yaml((EXAMPLES / "blinky.yaml").read_text(encoding="utf-8"))
    client.put("/api/cir/board", json=source.model_dump(mode="json"))

    got = client.get("/api/cir")
    assert got.status_code == 200
    assert got.json()["exists"] is True
    # The text the GET returns parses to the same Board the PUT wrote.
    assert parse_yaml(got.json()["text"]) == source


def test_put_cir_board_rejects_a_missing_required_field(client: TestClient) -> None:
    # Board requires `name`. FastAPI's body validation surfaces this as 422.
    response = client.put("/api/cir/board", json={"cir_version": "0.4"})
    assert response.status_code == 422
    # The broken board must not have been written.
    assert client.get("/api/cir").json()["exists"] is False


def test_put_cir_board_rejects_a_field_constraint_violation(
    client: TestClient,
) -> None:
    # FabTarget.layer_count must be ge=2; 1 violates the constraint.
    payload = {
        "cir_version": "0.4",
        "name": "bad-fab",
        "fab": {"name": "jlcpcb", "layer_count": 1},
    }
    response = client.put("/api/cir/board", json=payload)
    assert response.status_code == 422
    assert client.get("/api/cir").json()["exists"] is False
