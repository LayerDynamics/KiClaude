"""Integration tests for the agent FastAPI app."""

from __future__ import annotations

import os

import pytest
from agent import __version__, auth
from agent.main import app
from agent.session import claude_sdk_available
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_auth_cache() -> None:
    """The auth probe caches its result across calls — clear it between
    tests so monkeypatched env vars take effect immediately."""
    auth.reset_cache()
    yield
    auth.reset_cache()


def test_health_returns_ok_envelope(client: TestClient) -> None:
    """`GET /health` returns the gateway-aggregatable envelope."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "service": "agent", "version": __version__}


def test_echo_503_when_no_auth_path_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ANY accepted auth path, `POST /echo` returns 503 with a
    detail body that enumerates every option."""
    for var in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    ):
        monkeypatch.delenv(var, raising=False)
    # Force the keychain probe to fail by aiming PATH at a directory
    # with no `claude` binary — the probe's `shutil.which` lookup
    # then returns None and the early-exit path triggers.
    monkeypatch.setenv("PATH", "/var/empty")
    auth.reset_cache()

    resp = client.post("/echo", json={"prompt": "hi"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    # Enumerated paths are surfaced so the operator can pick.
    for expected in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "claude login",
    ):
        assert expected in detail, f"expected {expected!r} in detail body: {detail!r}"


def test_echo_accepts_anthropic_api_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ANTHROPIC_API_KEY set, the gate opens regardless of CLI
    state. We can't assert on the downstream SDK call without a real
    key, so we stop at the probe boundary."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    auth.reset_cache()
    result = auth.is_available()
    assert result.ok
    assert result.source == "env_api_key"


def test_echo_accepts_oauth_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CLAUDE_CODE_OAUTH_TOKEN` is enough on its own — no API key
    needed when running with a captured subscription token."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-token")
    auth.reset_cache()
    result = auth.is_available()
    assert result.ok
    assert result.source == "env_oauth_token"


def test_echo_accepts_bedrock_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    auth.reset_cache()
    result = auth.is_available()
    assert result.ok
    assert result.source == "bedrock"


def test_auth_status_endpoint_reports_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    auth.reset_cache()
    resp = client.get("/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["source"] == "env_api_key"
    assert "ANTHROPIC_API_KEY" in body["detail"]


def test_echo_validates_prompt(client: TestClient) -> None:
    """`POST /echo` with empty prompt is rejected by pydantic."""
    resp = client.post("/echo", json={"prompt": ""})
    assert resp.status_code == 422


def test_claude_sdk_importable() -> None:
    """Sanity: the SDK is on `PYTHONPATH` for the test env (uv sync brings it in)."""
    assert claude_sdk_available()


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    and os.environ.get("CLAUDE_CODE_USE_BEDROCK") != "1"
    and os.environ.get("CLAUDE_CODE_USE_VERTEX") != "1",
    reason=(
        "e2e gate: no env-based auth set. Run `claude login` first OR "
        "export ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN to exercise "
        "the live SDK round-trip."
    ),
)
def test_echo_round_trips_through_claude(client: TestClient) -> None:
    """e2e: with any accepted auth path, /echo round-trips a prompt
    through the SDK and surfaces the selected auth source."""
    resp = client.post("/echo", json={"prompt": "reply with the single word pong"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "pong" in body["reply"].lower()
    assert body["auth_source"] in {
        "env_api_key",
        "env_auth_token",
        "env_oauth_token",
        "bedrock",
        "vertex",
        "claude_cli_keychain",
    }
