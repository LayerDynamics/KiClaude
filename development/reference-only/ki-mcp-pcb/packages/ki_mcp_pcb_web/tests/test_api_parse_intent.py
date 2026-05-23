"""Integration tests for the natural-language intent endpoint (G4-T1).

The Anthropic SDK is never called in CI — every test stubs ``parse_nl``
on the server module, so the assertions exercise the endpoint's
contract (status codes + response shape) without a live API key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from ki_mcp_pcb_core.parsers.nl import (
    NLParserError,
    NLParseResult,
    NLParserUnavailableError,
)
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_web import server, session

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    return TestClient(server.app)


def _draft_result() -> NLParseResult:
    """A real NLParseResult built from blinky — the endpoint serialises it."""
    text = (EXAMPLES / "blinky.yaml").read_text(encoding="utf-8")
    return NLParseResult(board=parse_yaml(text), draft_yaml=text)


def test_parse_intent_returns_the_draft_board_and_yaml(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}
    draft = _draft_result()

    def fake_parse_nl(text: str) -> NLParseResult:
        seen["text"] = text
        return draft

    monkeypatch.setattr(server, "parse_nl", fake_parse_nl)

    response = client.post(
        "/api/parse_intent",
        json={"text": "ESP32-S3 dev board with one 100 nF cap"},
    )
    assert response.status_code == 200
    body = response.json()
    assert seen["text"] == "ESP32-S3 dev board with one 100 nF cap"
    assert body["draft_yaml"].startswith(("cir_version:", "# "))
    assert body["board"]["name"] == draft.board.name
    # The draft is NOT auto-written — the working CIR file stays absent.
    assert client.get("/api/cir").json()["exists"] is False


def test_parse_intent_503_when_anthropic_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_unavailable(_text: str) -> NLParseResult:
        raise NLParserUnavailableError(
            "Set ANTHROPIC_API_KEY to a valid Anthropic API key"
        )

    monkeypatch.setattr(server, "parse_nl", raise_unavailable)

    response = client.post(
        "/api/parse_intent", json={"text": "make me a board"}
    )
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_parse_intent_400_on_other_nl_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_parser_error(_text: str) -> NLParseResult:
        raise NLParserError("model returned no YAML block")

    monkeypatch.setattr(server, "parse_nl", raise_parser_error)

    response = client.post("/api/parse_intent", json={"text": "x"})
    assert response.status_code == 400
    assert "YAML" in response.json()["detail"]


def test_parse_intent_400_on_empty_prompt(client: TestClient) -> None:
    # Whitespace-only counts as empty.
    response = client.post("/api/parse_intent", json={"text": "   "})
    assert response.status_code == 400
    assert response.json()["detail"] == "empty prompt"


def test_parse_intent_strips_leading_and_trailing_whitespace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def fake(text: str) -> NLParseResult:
        seen["text"] = text
        return _draft_result()

    monkeypatch.setattr(server, "parse_nl", fake)

    client.post(
        "/api/parse_intent",
        json={"text": "   describe my board\n  "},
    )
    assert seen["text"] == "describe my board"
