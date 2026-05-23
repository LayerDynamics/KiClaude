"""Integration tests for the `kc_ping` MCP tool."""

from __future__ import annotations

import json

import pytest
from kc_mcp import __version__ as kc_mcp_version
from kc_mcp.server import build_server
from kc_mcp.tools.ping import kc_ping


def test_build_server_returns_config() -> None:
    """`build_server()` returns an SDK-shaped config with the kc_ping
    tool registered under name 'kiclaude'."""
    cfg = build_server()
    # McpSdkServerConfig is a TypedDict in claude_agent_sdk; we don't
    # over-bind on its shape — assert the dict-like shape we depend on.
    assert isinstance(cfg, dict)
    # Tool registration goes through the SDK; the config carries an
    # "instance" handle that the SDK uses to invoke tools.
    assert cfg.get("type") == "sdk"
    assert cfg.get("name") == "kiclaude"


@pytest.mark.asyncio
async def test_kc_ping_returns_all_four_keys() -> None:
    """The integration contract: `kc_ping({})` resolves to a dict with
    `ok`, `version`, `kcir_version`, `kicad_cli_version`."""
    # `@tool` decorates the function — invoke the underlying callable.
    result = await kc_ping.handler({})  # type: ignore[attr-defined]
    structured = result["structured"]
    assert structured["ok"] is True
    assert structured["version"] == kc_mcp_version
    assert "kcir_version" in structured
    assert "kicad_cli_version" in structured
    assert "ts" in structured


@pytest.mark.asyncio
async def test_kc_ping_json_text_block_round_trips() -> None:
    """The MCP-style text content block carries the same payload as
    the `structured` field — clients that only parse text get the
    same data."""
    result = await kc_ping.handler({})  # type: ignore[attr-defined]
    text = result["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed == result["structured"]
