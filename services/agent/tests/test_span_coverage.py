"""M1-Q-04 — subagent-aware OTel span coverage.

For each lifecycle hook in spec §8.3 (``PreToolUse``,
``PostToolUse``, ``SessionStart``, ``SessionEnd``), the test:

1. Invokes the hook with a representative payload (including a
   ``parent_session_id`` for the subagent variant).
2. Captures the spans emitted by the agent's tracer via the test
   `InMemorySpanExporter`.
3. Asserts every span carries the contract attributes:
   ``session_id``, ``parent_session_id`` (subagent only),
   ``tool_name`` (PreToolUse / PostToolUse), ``duration_ms``
   (PostToolUse).

Run with `uv run pytest services/agent/tests/test_span_coverage.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent.hooks.lifecycle import (
    post_tool_use,
    pre_tool_use,
    reset_inflight_for_tests,
    session_end,
    session_start,
)
from agent.telemetry import reset_for_tests


@pytest.fixture(autouse=True)
def fresh_inflight() -> None:
    reset_inflight_for_tests()


def _attrs(span: Any) -> dict[str, Any]:
    """Span attribute dict, regardless of OTel version. Some
    releases expose `.attributes` as a `BoundedAttributes` mapping —
    coerce to `dict` so `==` comparisons work."""
    raw = dict(span.attributes or {})
    return raw


def _by_name(spans: list[Any], name: str) -> list[Any]:
    return [s for s in spans if s.name == name]


async def test_pre_tool_use_emits_span_with_required_attrs() -> None:
    exporter = reset_for_tests()
    await pre_tool_use(
        {
            "tool_name": "kc_symbol_add",
            "tool_use_id": "tu-1",
            "session_id": "sess-A",
            "tool_input": {"project_id": "proj-1"},
        },
        None,
        None,
    )
    spans = _by_name(exporter.get_finished_spans(), "agent.hook.pre_tool_use")
    assert len(spans) == 1
    attrs = _attrs(spans[0])
    assert attrs["hook_event"] == "PreToolUse"
    assert attrs["session_id"] == "sess-A"
    assert attrs["tool_name"] == "kc_symbol_add"
    assert attrs["tool_use_id"] == "tu-1"
    assert attrs["project_id"] == "proj-1"
    # No subagent → parent_session_id must not be set.
    assert "parent_session_id" not in attrs


async def test_post_tool_use_emits_span_with_duration_and_ok() -> None:
    exporter = reset_for_tests()
    # Pair Pre/Post so the PostToolUse span carries a real duration.
    await pre_tool_use(
        {
            "tool_name": "kc_wire_connect",
            "tool_use_id": "tu-2",
            "session_id": "sess-B",
            "tool_input": {"project_id": "proj-1"},
        },
        None,
        None,
    )
    await post_tool_use(
        {
            "tool_name": "kc_wire_connect",
            "tool_use_id": "tu-2",
            "session_id": "sess-B",
            "tool_response": {"structured": {"ok": True}},
        },
        None,
        None,
    )
    spans = _by_name(exporter.get_finished_spans(), "agent.hook.post_tool_use")
    assert len(spans) == 1
    attrs = _attrs(spans[0])
    assert attrs["hook_event"] == "PostToolUse"
    assert attrs["session_id"] == "sess-B"
    assert attrs["tool_name"] == "kc_wire_connect"
    assert attrs["tool_use_id"] == "tu-2"
    assert attrs["ok"] is True
    assert isinstance(attrs["duration_ms"], (int, float))
    assert attrs["duration_ms"] >= 0.0


async def test_session_start_and_session_end_emit_spans() -> None:
    exporter = reset_for_tests()
    await session_start(
        {"session_id": "sess-C", "cwd": "/tmp/proj", "agent_id": "kiclaude-main"},
        None,
        None,
    )
    await session_end({"session_id": "sess-C"}, None, None)
    starts = _by_name(exporter.get_finished_spans(), "agent.hook.session_start")
    ends = _by_name(exporter.get_finished_spans(), "agent.hook.session_end")
    assert len(starts) == 1
    assert len(ends) == 1
    s_attrs = _attrs(starts[0])
    e_attrs = _attrs(ends[0])
    assert s_attrs["hook_event"] == "SessionStart"
    assert s_attrs["session_id"] == "sess-C"
    assert s_attrs["cwd"] == "/tmp/proj"
    assert s_attrs["agent_id"] == "kiclaude-main"
    assert e_attrs["hook_event"] == "SessionEnd"
    assert e_attrs["session_id"] == "sess-C"


async def test_subagent_invocation_propagates_parent_session_id() -> None:
    """When a hook fires inside a delegated subagent, the SDK
    forwards `parent_session_id` on `input_data`. The span must
    carry that attribute so traces can link subagent → parent."""
    exporter = reset_for_tests()
    await pre_tool_use(
        {
            "tool_name": "kc_validate",
            "tool_use_id": "tu-sub-1",
            "session_id": "sess-child",
            "parent_session_id": "sess-parent",
            "tool_input": {"project_id": "proj-2"},
        },
        None,
        None,
    )
    spans = _by_name(exporter.get_finished_spans(), "agent.hook.pre_tool_use")
    assert spans, "no PreToolUse span emitted for the subagent call"
    attrs = _attrs(spans[-1])
    assert attrs["session_id"] == "sess-child"
    assert attrs["parent_session_id"] == "sess-parent"


async def test_every_section_8_3_hook_has_span_coverage() -> None:
    """Drive every hook once + assert all four spans landed in the
    exporter. The aggregate gate the M1-Q-04 plan calls for."""
    exporter = reset_for_tests()
    await session_start(
        {"session_id": "sess-X", "cwd": "/tmp/p", "agent_id": "kiclaude-main"},
        None,
        None,
    )
    await pre_tool_use(
        {
            "tool_name": "kc_symbol_edit",
            "tool_use_id": "tu-X",
            "session_id": "sess-X",
            "tool_input": {"project_id": "proj-X"},
        },
        None,
        None,
    )
    await post_tool_use(
        {
            "tool_name": "kc_symbol_edit",
            "tool_use_id": "tu-X",
            "session_id": "sess-X",
            "tool_response": {"structured": {"ok": True}},
        },
        None,
        None,
    )
    await session_end({"session_id": "sess-X"}, None, None)

    names = {s.name for s in exporter.get_finished_spans()}
    required = {
        "agent.hook.session_start",
        "agent.hook.pre_tool_use",
        "agent.hook.post_tool_use",
        "agent.hook.session_end",
    }
    missing = required - names
    assert not missing, f"hooks missing span coverage: {missing}"
