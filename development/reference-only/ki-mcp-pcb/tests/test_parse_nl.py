"""parse_nl tests — mock Anthropic client.

Real-API tests run only when ANTHROPIC_API_KEY is set; see
tests/llm_evals/.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from ki_mcp_pcb_core.parsers.nl import (
    NLParserError,
    NLParserUnavailableError,
    _extract_yaml,
    parse_nl,
    parse_nl_with_client,
)

# ---------------------------------------------------------------------------
# YAML extraction
# ---------------------------------------------------------------------------


def test_extract_yaml_from_fenced_block() -> None:
    text = "Here's the board:\n\n```yaml\ncir_version: \"0.4\"\nname: x\n```\n"
    assert _extract_yaml(text).startswith('cir_version: "0.4"')


def test_extract_yaml_without_lang_tag() -> None:
    text = "```\nfoo: bar\n```"
    assert _extract_yaml(text).strip() == "foo: bar"


def test_extract_yaml_falls_back_to_full_text() -> None:
    text = "cir_version: \"0.4\"\nname: x"
    assert _extract_yaml(text) == text.strip()


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------


def _make_mock_client(response_text: str):
    """Build a fake client that mimics the Anthropic SDK shape."""
    calls: list[dict] = []

    response = SimpleNamespace(
        content=[SimpleNamespace(text=response_text)],
    )

    def create(**kwargs):
        calls.append(kwargs)
        return response

    class _Messages:
        def __init__(self) -> None:
            self.create = create

    client = SimpleNamespace(messages=_Messages(), _calls=calls)
    return client


_VALID_YAML_RESPONSE = """\
Here is the board:

```yaml
cir_version: "0.4"
name: smoke
components:
  - refdes: U1
    mpn: ESP32-S3-WROOM-1
  - refdes: C1
    mpn: GRM188R71C104KA01D
    value: "100nF"
nets:
  - name: GND
    net_class: ground
    members: ["U1.1", "C1.2"]
  - name: "3V3"
    net_class: power
    power_rail: "3V3"
    members: ["U1.2", "C1.1"]
fab:
  name: jlcpcb
  layer_count: 2
```
"""


# ---------------------------------------------------------------------------
# parse_nl_with_client (the unit-testable surface)
# ---------------------------------------------------------------------------


def test_parse_nl_with_mock_returns_validated_board() -> None:
    client = _make_mock_client(_VALID_YAML_RESPONSE)
    result = parse_nl_with_client(client, "ESP32-S3 dev board with one 100 nF cap")
    assert result.board.name == "smoke"
    assert len(result.board.components) == 2
    assert any(n.name == "GND" for n in result.board.nets)


def test_parse_nl_writes_draft_path_when_given(tmp_path: Path) -> None:
    client = _make_mock_client(_VALID_YAML_RESPONSE)
    draft = tmp_path / "draft.yaml"
    result = parse_nl_with_client(client, "a thing", draft_path=draft)
    assert draft.exists()
    contents = draft.read_text(encoding="utf-8")
    assert "cir_version" in contents
    assert "ESP32-S3-WROOM-1" in contents
    # Returned draft_yaml should match what was written.
    assert contents.strip() == result.draft_yaml.strip()


def test_parse_nl_raises_clean_error_on_invalid_yaml() -> None:
    client = _make_mock_client("```yaml\nnot: valid: yaml: nested\n  bad ::\n```")
    with pytest.raises(NLParserError):
        parse_nl_with_client(client, "any prompt")


def test_parse_nl_passes_schema_to_model() -> None:
    """The user-message we send must include the CIR JSON Schema."""
    client = _make_mock_client(_VALID_YAML_RESPONSE)
    parse_nl_with_client(client, "two-layer ESP32-S3 board")
    call = client._calls[0]
    user_message = call["messages"][0]["content"]
    assert "JSON Schema" in user_message
    # The schema references the Board class
    assert '"Board"' in user_message or "Board" in user_message


def test_parse_nl_passes_system_prompt() -> None:
    client = _make_mock_client(_VALID_YAML_RESPONSE)
    parse_nl_with_client(client, "any prompt")
    call = client._calls[0]
    system = call["system"]
    assert "ki-mcp-pcb" in system
    assert "MPN" in system


# ---------------------------------------------------------------------------
# parse_nl (the SDK + env-var entrypoint)
# ---------------------------------------------------------------------------


def test_parse_nl_without_api_key_raises_unavailable(monkeypatch) -> None:
    """Even if the SDK is installed, no API key → clear unavailable error."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # The anthropic package may not be installed in CI; the unavailable
    # check fires either way.
    with pytest.raises(NLParserUnavailableError):
        parse_nl("hi")


def test_parse_nl_with_explicit_api_key_attempts_sdk(monkeypatch) -> None:
    """Passing api_key= bypasses the env-var check and tries the SDK.

    If anthropic isn't installed we get NLParserUnavailableError; if it
    IS installed the call would proceed but we don't actually want to
    hit the API in the test. Skip if SDK is present (covered elsewhere).
    """
    try:
        import anthropic  # noqa: F401
    except ImportError:
        with pytest.raises(NLParserUnavailableError):
            parse_nl("hi", api_key="sk-fake")
    else:
        pytest.skip("anthropic installed; skipping no-key path test")


# ---------------------------------------------------------------------------
# Live API test — opt-in via env var
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    "ANTHROPIC_API_KEY" not in os.environ,
    reason="Set ANTHROPIC_API_KEY to run live parse_nl test",
)
def test_parse_nl_live_smoke() -> None:  # pragma: no cover — live API
    """Real Anthropic call. Opt-in only via env var."""
    result = parse_nl(
        "Two-layer board: ESP32-S3-WROOM-1 with one 100 nF decoupling "
        "cap to GND, JLCPCB-fabbable."
    )
    assert any(c.mpn.startswith("ESP32") for c in result.board.components)
