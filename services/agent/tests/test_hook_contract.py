"""Integration tests for the M0-P-06 lifecycle hook wiring.

Hooks are pure async callables — we exercise them directly with
fabricated input dicts and assert the JSONL shape on a captured sink.
This covers the M0-Q-05 OTel contract without spinning up a live
ClaudeSDKClient session (which requires an API key).
"""

from __future__ import annotations

import io
import json

import pytest
from agent import hooks
from agent.bridge import build_options
from agent.hooks import lifecycle


@pytest.fixture(autouse=True)
def fresh_sink_and_inflight() -> io.StringIO:
    """Swap the module-level SINK for a captured StringIO before each
    test, and reset the inflight map. The hooks read `SINK` off the
    `lifecycle` module each call, so the rebind must target that
    module (the `agent.hooks` re-export is just an alias).
    """
    buf = io.StringIO()
    lifecycle.SINK = lifecycle.HookSink(stream=buf)
    hooks.reset_inflight_for_tests()
    return buf


def _lines(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line]


@pytest.mark.asyncio
async def test_pre_then_post_emit_jsonl_pair(fresh_sink_and_inflight: io.StringIO) -> None:
    """PreToolUse + PostToolUse for the same tool_use_id emit two lines
    with all the M0-Q-05 contract fields, and PostToolUse's
    `duration_ms` is non-negative."""
    payload_pre = {
        "session_id": "S1",
        "tool_use_id": "T1",
        "tool_name": "kc_ping",
        "tool_input": {"project_id": "P1"},
    }
    await hooks.pre_tool_use(payload_pre, "T1", None)

    payload_post = {
        "session_id": "S1",
        "tool_use_id": "T1",
        "tool_name": "kc_ping",
        "tool_input": {"project_id": "P1"},
        "tool_response": {"structured": {"ok": True, "version": "0.1.0"}},
    }
    await hooks.post_tool_use(payload_post, "T1", None)

    emitted = _lines(fresh_sink_and_inflight)
    assert len(emitted) == 2

    pre = emitted[0]
    assert pre["event"] == "PreToolUse"
    assert pre["tool_name"] == "kc_ping"
    assert pre["session_id"] == "S1"
    assert pre["tool_use_id"] == "T1"
    assert pre["project_id"] == "P1"
    assert isinstance(pre["ts"], str) and pre["ts"]

    post = emitted[1]
    assert post["event"] == "PostToolUse"
    assert post["tool_name"] == "kc_ping"
    assert post["session_id"] == "S1"
    assert post["project_id"] == "P1"
    assert post["ok"] is True
    assert isinstance(post["duration_ms"], (int, float))
    assert post["duration_ms"] >= 0.0


@pytest.mark.asyncio
async def test_post_tool_use_failure_flag(fresh_sink_and_inflight: io.StringIO) -> None:
    """`ok=False` when the tool response carries `isError=True` or a
    structured `ok=False`."""
    await hooks.pre_tool_use(
        {"session_id": "S", "tool_use_id": "T2", "tool_name": "kc_ping", "tool_input": {}},
        "T2",
        None,
    )
    await hooks.post_tool_use(
        {
            "session_id": "S",
            "tool_use_id": "T2",
            "tool_name": "kc_ping",
            "tool_input": {},
            "tool_response": {"isError": True, "content": [{"type": "text", "text": "boom"}]},
        },
        "T2",
        None,
    )
    post = _lines(fresh_sink_and_inflight)[-1]
    assert post["ok"] is False


@pytest.mark.asyncio
async def test_session_start_and_end(fresh_sink_and_inflight: io.StringIO) -> None:
    await hooks.session_start({"session_id": "S", "cwd": "/tmp", "agent_id": "A"}, None, None)
    await hooks.session_end({"session_id": "S"}, None, None)
    lines = _lines(fresh_sink_and_inflight)
    assert [line["event"] for line in lines] == ["SessionStart", "SessionEnd"]


@pytest.mark.asyncio
async def test_otel_contract_field_enumeration(fresh_sink_and_inflight: io.StringIO) -> None:
    """M0-Q-05 contract: PreToolUse + PostToolUse JSONL must carry the
    exact field set documented in the plan. This test enumerates the
    fields explicitly so any future drift trips a clear assertion."""
    pre_required = {"event", "ts", "tool_name", "session_id", "project_id", "tool_use_id"}
    post_required = {
        "event",
        "ts",
        "tool_name",
        "session_id",
        "project_id",
        "tool_use_id",
        "duration_ms",
        "ok",
    }

    await hooks.pre_tool_use(
        {
            "session_id": "S-otel",
            "tool_use_id": "T-otel",
            "tool_name": "kc_ping",
            "tool_input": {"project_id": "P-otel"},
        },
        "T-otel",
        None,
    )
    await hooks.post_tool_use(
        {
            "session_id": "S-otel",
            "tool_use_id": "T-otel",
            "tool_name": "kc_ping",
            "tool_input": {"project_id": "P-otel"},
            "tool_response": {"structured": {"ok": True}},
        },
        "T-otel",
        None,
    )

    pre, post = _lines(fresh_sink_and_inflight)
    assert pre_required.issubset(pre.keys()), (
        f"PreToolUse missing required contract fields: {pre_required - pre.keys()}"
    )
    assert post_required.issubset(post.keys()), (
        f"PostToolUse missing required contract fields: {post_required - post.keys()}"
    )
    # PreToolUse must NOT carry duration_ms / ok (those are PostToolUse-only).
    assert "duration_ms" not in pre
    assert "ok" not in pre


def test_build_options_wires_kc_mcp_and_hooks() -> None:
    """`build_options()` returns a ClaudeAgentOptions with the kiclaude
    MCP server and all four lifecycle hook categories registered."""
    opts = build_options()
    assert "kiclaude" in opts.mcp_servers
    assert opts.mcp_servers["kiclaude"]["name"] == "kiclaude"
    assert opts.hooks is not None
    expected_events = {"PreToolUse", "PostToolUse", "SessionStart", "Stop"}
    assert set(opts.hooks.keys()) == expected_events
    for event in expected_events:
        matchers = opts.hooks[event]
        assert matchers, f"no matcher for {event}"
        assert matchers[0].hooks, f"no callbacks for {event}"
