"""Web viewer REST API tests.

Smoke + happy-path tests using FastAPI's TestClient. The viewer HTML
itself is exercised by the static-asset endpoint test plus a couple of
sanity asserts on the JS bundle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from ki_mcp_pcb_web.server import app

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

client = TestClient(app)


# ---------------------------------------------------------------------------
# Meta + static
# ---------------------------------------------------------------------------


def test_version_endpoint_returns_versions() -> None:
    r = client.get("/api/version")
    assert r.status_code == 200
    payload = r.json()
    assert "core_version" in payload
    assert "cir_version" in payload


def test_index_returns_html() -> None:
    # `/` serves the built GUI SPA when present, else the legacy viewer —
    # assert on the contract both honour: an HTML document.
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<!doctype html" in r.text.lower()


def test_static_app_js_served() -> None:
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "uploadAndValidate" in r.text


# ---------------------------------------------------------------------------
# /api/validate
# ---------------------------------------------------------------------------


def _yaml_file(path: Path):
    return ("blinky.yaml", path.read_bytes(), "application/x-yaml")


def test_validate_endpoint_happy_path() -> None:
    with EXAMPLES.joinpath("blinky.yaml").open("rb") as fh:
        r = client.post("/api/validate", files={"file": ("blinky.yaml", fh, "application/x-yaml")})
    assert r.status_code == 200
    data = r.json()
    assert data["board"]["name"]
    assert isinstance(data["validation"]["issues"], list)
    assert len(data["bom"]) >= 1
    assert len(data["sourcing"]) >= 1


def test_validate_endpoint_returns_400_on_bad_yaml() -> None:
    r = client.post("/api/validate", files={"file": ("bad.yaml", b"not: valid: yaml: :: ::", "application/x-yaml")})
    assert r.status_code == 400


def test_validate_includes_components_and_nets() -> None:
    with EXAMPLES.joinpath("stm32_audio.yaml").open("rb") as fh:
        r = client.post("/api/validate", files={"file": ("stm32.yaml", fh, "application/x-yaml")})
    assert r.status_code == 200
    data = r.json()
    refdes = {c["refdes"] for c in data["board"]["components"]}
    assert "U1" in refdes and "U2" in refdes
    net_names = {n["name"] for n in data["board"]["nets"]}
    assert "GND" in net_names


# ---------------------------------------------------------------------------
# /api/diff
# ---------------------------------------------------------------------------


def test_diff_endpoint_identical_boards() -> None:
    with EXAMPLES.joinpath("blinky.yaml").open("rb") as left, \
         EXAMPLES.joinpath("blinky.yaml").open("rb") as right:
        r = client.post(
            "/api/diff",
            files={
                "left": ("blinky.yaml", left, "application/x-yaml"),
                "right": ("blinky.yaml", right, "application/x-yaml"),
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["identical"] is True


def test_diff_endpoint_different_boards() -> None:
    with EXAMPLES.joinpath("blinky.yaml").open("rb") as left, \
         EXAMPLES.joinpath("stm32_audio.yaml").open("rb") as right:
        r = client.post(
            "/api/diff",
            files={
                "left": ("blinky.yaml", left, "application/x-yaml"),
                "right": ("stm32.yaml", right, "application/x-yaml"),
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["identical"] is False
    assert data["components_added"] or data["components_removed"]


# ---------------------------------------------------------------------------
# /api/impedance
# ---------------------------------------------------------------------------


def test_impedance_endpoint_runs_on_high_speed_demo() -> None:
    """USB+Eth demo declares 6 nets with target_impedance_ohm."""
    with EXAMPLES.joinpath("usb_eth_phy.yaml").open("rb") as fh:
        r = client.post("/api/impedance", files={"file": ("usb.yaml", fh, "application/x-yaml")})
    assert r.status_code == 200
    rows = r.json()["rows"]
    # 2 USB + 2 ETH_TX + 2 ETH_RX = 6
    assert len(rows) == 6
    # Solver-tuned geometry hits the targets within band
    for row in rows:
        assert row["achieved_ohm"] is not None


def test_impedance_endpoint_empty_when_no_targets() -> None:
    with EXAMPLES.joinpath("blinky.yaml").open("rb") as fh:
        r = client.post("/api/impedance", files={"file": ("blinky.yaml", fh, "application/x-yaml")})
    assert r.status_code == 200
    assert r.json()["rows"] == []
