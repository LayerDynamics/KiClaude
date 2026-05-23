"""Integration tests for the agent FastAPI app."""

from __future__ import annotations

import os

import pytest
from agent import __version__
from agent.main import app
from agent.session import claude_sdk_available
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok_envelope(client: TestClient) -> None:
    """`GET /health` returns the gateway-aggregatable envelope."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "service": "agent", "version": __version__}


def test_echo_requires_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `ANTHROPIC_API_KEY`, `POST /echo` returns 503 (not 500)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/echo", json={"prompt": "hi"})
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_echo_validates_prompt(client: TestClient) -> None:
    """`POST /echo` with empty prompt is rejected by pydantic."""
    resp = client.post("/echo", json={"prompt": ""})
    assert resp.status_code == 422


def test_claude_sdk_importable() -> None:
    """Sanity: the SDK is on `PYTHONPATH` for the test env (uv sync brings it in)."""
    assert claude_sdk_available()


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="e2e gate: ANTHROPIC_API_KEY not set",
)
def test_echo_round_trips_through_claude(client: TestClient) -> None:
    """e2e: with an API key set, /echo round-trips a prompt through the SDK."""
    resp = client.post("/echo", json={"prompt": "reply with the single word pong"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "pong" in body["reply"].lower()
