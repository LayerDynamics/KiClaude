"""Integration tests for the G3 result-pane endpoints.

Covers ``GET /api/decoupling_check``, ``GET /api/return_path_check`` and
``POST /api/diff/working`` — the new endpoints the form/results panes call.
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

# A board that names a 3V3 power rail but never adds a decoupling cap —
# CIR030 must flag this. (Constructed inline so the failure is obvious.)
_MISSING_DECAP_CIR = textwrap.dedent(
    """
    cir_version: "0.4"
    name: missing-decap
    components:
      - refdes: U1
        mpn: ESP32-S3-WROOM-1
        decoupling_pins: ["1"]
    nets:
      - name: 3V3
        net_class: power
        members: ["U1.1"]
        power_rail: "3V3"
      - name: GND
        net_class: ground
        members: ["U1.2"]
    """
).strip()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    return TestClient(app)


def _seed_working_cir(client: TestClient, example: str) -> None:
    """PUT one of the repo's example CIRs as the working CIR."""
    text = (EXAMPLES / example).read_text(encoding="utf-8")
    response = client.put("/api/cir", json={"text": text})
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# /api/decoupling_check
# --------------------------------------------------------------------------
def test_decoupling_check_passes_for_a_well_covered_board(
    client: TestClient,
) -> None:
    # stm32_audio declares decoupling on U1 and U2 and has matching caps.
    _seed_working_cir(client, "stm32_audio.yaml")

    response = client.get("/api/decoupling_check")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["issues"] == []
    # The MCUs that declared decoupling are listed for the GUI to highlight.
    assert set(body["ics_with_decoupling_declared"]) >= {"U1", "U2"}


def test_decoupling_check_flags_a_power_rail_without_a_cap(
    client: TestClient,
) -> None:
    client.put("/api/cir", json={"text": _MISSING_DECAP_CIR})

    response = client.get("/api/decoupling_check")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    codes = {i["code"] for i in body["issues"]}
    assert "CIR030" in codes


def test_decoupling_check_400s_when_no_working_cir(client: TestClient) -> None:
    response = client.get("/api/decoupling_check")
    assert response.status_code == 400
    assert "no working CIR" in response.json()["detail"]


# --------------------------------------------------------------------------
# /api/return_path_check
# --------------------------------------------------------------------------
def test_return_path_check_warns_on_high_speed_nets_without_a_plane(
    client: TestClient,
) -> None:
    # stm32_audio has I2S high_speed nets but no reference_plane → CIR090 warns.
    _seed_working_cir(client, "stm32_audio.yaml")

    response = client.get("/api/return_path_check")
    assert response.status_code == 200
    body = response.json()
    assert any(i["code"] == "CIR090" for i in body["issues"])
    hs = {entry["net"] for entry in body["high_speed_nets"]}
    assert "I2S_BCLK" in hs


def test_return_path_check_clean_when_high_speed_nets_have_a_plane(
    client: TestClient,
) -> None:
    # usb_eth_phy declares reference_plane on every differential net.
    _seed_working_cir(client, "usb_eth_phy.yaml")

    response = client.get("/api/return_path_check")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert all(entry["reference_plane"] for entry in body["high_speed_nets"])


def test_return_path_check_400s_when_no_working_cir(client: TestClient) -> None:
    response = client.get("/api/return_path_check")
    assert response.status_code == 400


# --------------------------------------------------------------------------
# /api/diff/working
# --------------------------------------------------------------------------
def test_diff_working_identical_returns_no_changes(client: TestClient) -> None:
    _seed_working_cir(client, "blinky.yaml")

    baseline = (EXAMPLES / "blinky.yaml").read_bytes()
    response = client.post(
        "/api/diff/working",
        files={"baseline": ("blinky.yaml", baseline, "application/x-yaml")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identical"] is True
    assert body["components_added"] == []
    assert body["components_removed"] == []


def test_diff_working_shows_added_and_removed_components(
    client: TestClient,
) -> None:
    # working CIR is the simple blinky; the baseline is the larger stm32_audio.
    # Diff reads baseline -> working: things only-in-blinky are "added",
    # things only-in-stm32_audio are "removed".
    _seed_working_cir(client, "blinky.yaml")
    baseline = (EXAMPLES / "stm32_audio.yaml").read_bytes()

    response = client.post(
        "/api/diff/working",
        files={"baseline": ("stm32_audio.yaml", baseline, "application/x-yaml")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identical"] is False
    # The two boards have different names + entirely different component sets.
    assert body["name_changed"] is not None
    assert body["components_removed"]  # stm32_audio parts not in blinky


def test_diff_working_400s_when_no_working_cir(client: TestClient) -> None:
    baseline = (EXAMPLES / "blinky.yaml").read_bytes()
    response = client.post(
        "/api/diff/working",
        files={"baseline": ("blinky.yaml", baseline, "application/x-yaml")},
    )
    assert response.status_code == 400
    assert "no working CIR" in response.json()["detail"]


def test_diff_working_rejects_a_broken_baseline(client: TestClient) -> None:
    _seed_working_cir(client, "blinky.yaml")
    response = client.post(
        "/api/diff/working",
        files={
            "baseline": (
                "broken.yaml",
                b"[unterminated flow sequence",
                "application/x-yaml",
            )
        },
    )
    assert response.status_code == 400
    assert "parse error" in response.json()["detail"]
