"""Integration tests for the kiconnector FastAPI app."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from kiconnector import __version__
from kiconnector.main import app
from kiconnector.subprocess import probe_version


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok_envelope(client: TestClient) -> None:
    """`GET /health` returns the standard gateway envelope."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "service": "kiconnector",
        "version": __version__,
    }


def test_tools_versions_returns_three_keys(client: TestClient) -> None:
    """`GET /tools/versions` always returns the three required keys —
    each is either a version string or `"not installed"`."""
    resp = client.get("/tools/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"kicad_cli", "freerouting", "kikit"}
    for value in body.values():
        assert isinstance(value, str)
        assert value, "version field must be non-empty"


@pytest.mark.asyncio
async def test_probe_version_missing_binary_reports_not_installed() -> None:
    """`probe_version` for a non-existent binary returns `not installed`."""
    result = await probe_version("this_binary_does_not_exist_xyz_42")
    assert result.available is False
    assert result.version == "not installed"


@pytest.mark.asyncio
async def test_probe_version_echo_returns_output() -> None:
    """`probe_version("echo", ("hello",))` returns the echoed text —
    proves the subprocess pipeline actually works end-to-end."""
    result = await probe_version("echo", ("hello",))
    assert result.available is True
    assert "hello" in result.version


def test_kicad_cli_version_shape_if_installed(client: TestClient) -> None:
    """Conditional: when kicad-cli is on PATH, the version field is a
    non-trivial string (KiCad ships its version as e.g. '9.0.0'). When
    it isn't, the field equals 'not installed' — that's covered by the
    `_returns_three_keys` test."""
    resp = client.get("/tools/versions")
    value = resp.json()["kicad_cli"]
    if value == "not installed":
        pytest.skip("kicad-cli not installed in this environment")
    # Anything resembling a semver dot-separated number qualifies.
    assert re.search(r"\d+\.\d+", value), f"unexpected kicad-cli version: {value!r}"
