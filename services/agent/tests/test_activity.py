"""M3-T-09 — Activity registry + HTTP snapshot endpoint.

Two layers of coverage:

- Registry semantics: session/tool lifecycle recording, parent linkage,
  late-arriving start, the snapshot delta filter (`since_seq`), ring-
  buffer capping.
- Lifecycle-hook integration: simulating the same `pre_tool_use` /
  `post_tool_use` / `session_start` / `session_end` calls the real
  Claude Agent SDK fires, and verifying the registry sees them.
- FastAPI endpoint contract: GET /activity/snapshot returns the
  expected shape; DELETE /activity clears.
"""

from __future__ import annotations

import pytest
from agent import activity
from agent.hooks.lifecycle import (
    post_tool_use,
    pre_tool_use,
    session_end,
    session_start,
)
from agent.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    activity.reset_registry_for_tests()
    yield
    activity.reset_registry_for_tests()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------
# Registry-level
# ---------------------------------------------------------------------


async def test_session_start_then_tool_call_then_tool_end_round_trip() -> None:
    reg = activity.registry()
    await reg.record_session_start(session_id="s1", agent_id="orchestrator")
    await reg.record_tool_start(
        tool_use_id="t1",
        session_id="s1",
        tool_name="mcp__kiclaude__kc_kcir_get",
        project_id="proj-1",
    )
    snap = await reg.snapshot()
    assert len(snap["sessions"]) == 1
    assert len(snap["calls"]) == 1
    assert snap["calls"][0]["status"] == "running"
    assert snap["calls"][0]["ok"] is None
    await reg.record_tool_end(tool_use_id="t1", ok=True, duration_ms=42.5)
    snap = await reg.snapshot()
    assert snap["calls"][0]["status"] == "ok"
    assert snap["calls"][0]["ok"] is True
    assert snap["calls"][0]["duration_ms"] == 42.5
    assert snap["calls"][0]["ended_at"] is not None


async def test_subagent_session_records_parent_link() -> None:
    """The panel's tree view depends on `parent_session_id` being
    captured on either SessionStart or the first tool call."""
    reg = activity.registry()
    await reg.record_session_start(session_id="orch", agent_id="")
    await reg.record_session_start(
        session_id="child", agent_id="decoupling-auditor", parent_session_id="orch"
    )
    snap = await reg.snapshot()
    by_id = {s["session_id"]: s for s in snap["sessions"]}
    assert by_id["orch"]["parent_session_id"] is None
    assert by_id["child"]["parent_session_id"] == "orch"
    assert by_id["child"]["agent_id"] == "decoupling-auditor"


async def test_tool_start_auto_registers_late_session_with_parent() -> None:
    """The agent SDK sometimes fires PreToolUse before SessionStart
    for the subagent — the registry must create a stub session so the
    panel still sees the call."""
    reg = activity.registry()
    await reg.record_tool_start(
        tool_use_id="t1",
        session_id="orphan",
        tool_name="kc_kcir_get",
        project_id=None,
        parent_session_id="orch",
    )
    snap = await reg.snapshot()
    by_id = {s["session_id"]: s for s in snap["sessions"]}
    assert "orphan" in by_id
    assert by_id["orphan"]["parent_session_id"] == "orch"


async def test_session_start_idempotent_refresh_only_when_field_empty() -> None:
    """SessionStart can fire more than once (SDK resume). The
    registry should not clobber a known `agent_id` with an empty one
    on the re-fire."""
    reg = activity.registry()
    await reg.record_session_start(
        session_id="s1", agent_id="decoupling-auditor"
    )
    await reg.record_session_start(session_id="s1", agent_id="")
    snap = await reg.snapshot()
    assert snap["sessions"][0]["agent_id"] == "decoupling-auditor"


async def test_tool_end_without_matching_start_is_ignored() -> None:
    reg = activity.registry()
    result = await reg.record_tool_end(tool_use_id="ghost", ok=True, duration_ms=1.0)
    assert result is None
    snap = await reg.snapshot()
    assert snap["calls"] == []


async def test_session_end_with_unknown_session_records_stub() -> None:
    """If SessionEnd fires for a session the registry never saw a
    SessionStart for, surface a synthetic stub — the panel needs the
    lifecycle complete even when the start went missing."""
    reg = activity.registry()
    await reg.record_session_end(session_id="never-started")
    snap = await reg.snapshot()
    assert any(s["session_id"] == "never-started" for s in snap["sessions"])
    by_id = {s["session_id"]: s for s in snap["sessions"]}
    assert by_id["never-started"]["ended_at"] is not None


async def test_snapshot_since_seq_returns_only_new_entries() -> None:
    reg = activity.registry()
    await reg.record_session_start(session_id="s1", agent_id="")
    snap1 = await reg.snapshot()
    hw1 = snap1["high_water_seq"]
    # No further activity — the second snapshot should be empty.
    snap2 = await reg.snapshot(since_seq=hw1)
    assert snap2["sessions"] == []
    assert snap2["calls"] == []
    # Now add more activity and confirm the delta.
    await reg.record_tool_start(
        tool_use_id="t1", session_id="s1", tool_name="kc_x", project_id=None
    )
    snap3 = await reg.snapshot(since_seq=hw1)
    assert len(snap3["calls"]) == 1
    assert snap3["calls"][0]["tool_use_id"] == "t1"


async def test_ring_buffer_caps_at_max_entries() -> None:
    activity.reset_registry_for_tests(max_entries=3)
    reg = activity.registry()
    for i in range(5):
        await reg.record_session_start(session_id=f"s{i}", agent_id="")
    snap = await reg.snapshot()
    # Oldest two dropped; last three retained, in insertion order.
    assert [s["session_id"] for s in snap["sessions"]] == ["s2", "s3", "s4"]


# ---------------------------------------------------------------------
# Hook integration — the real callbacks should populate the registry.
# ---------------------------------------------------------------------


async def test_pre_and_post_tool_use_hooks_feed_the_registry() -> None:
    await session_start(
        {"session_id": "orch", "agent_id": "", "cwd": "/tmp"},
        None,
        {},
    )
    await pre_tool_use(
        {
            "session_id": "orch",
            "tool_name": "mcp__kiclaude__kc_kcir_get",
            "tool_use_id": "call-1",
            "tool_input": {"project_id": "proj-1"},
        },
        None,
        {},
    )
    await post_tool_use(
        {
            "session_id": "orch",
            "tool_name": "mcp__kiclaude__kc_kcir_get",
            "tool_use_id": "call-1",
            "tool_response": {"content": [{"type": "text", "text": "ok"}]},
        },
        None,
        {},
    )
    await session_end({"session_id": "orch"}, None, {})

    snap = await activity.registry().snapshot()
    assert len(snap["sessions"]) == 1
    assert snap["sessions"][0]["session_id"] == "orch"
    assert snap["sessions"][0]["ended_at"] is not None
    assert len(snap["calls"]) == 1
    call = snap["calls"][0]
    assert call["tool_use_id"] == "call-1"
    assert call["tool_name"] == "mcp__kiclaude__kc_kcir_get"
    assert call["status"] == "ok"
    assert call["project_id"] == "proj-1"


async def test_subagent_dispatch_threads_parent_session_id_into_registry() -> None:
    """Pin the per-subagent visibility: when a PreToolUse fires with
    `parent_session_id` set, the registry's session record for the
    child session carries that link so the panel can render the tree."""
    await session_start(
        {
            "session_id": "child",
            "parent_session_id": "orch",
            "agent_id": "decoupling-auditor",
        },
        None,
        {},
    )
    await pre_tool_use(
        {
            "session_id": "child",
            "parent_session_id": "orch",
            "tool_name": "mcp__kiclaude__kc_kcir_get",
            "tool_use_id": "call-c1",
            "tool_input": {},
        },
        None,
        {},
    )
    snap = await activity.registry().snapshot()
    by_id = {s["session_id"]: s for s in snap["sessions"]}
    assert by_id["child"]["parent_session_id"] == "orch"
    assert by_id["child"]["agent_id"] == "decoupling-auditor"


# ---------------------------------------------------------------------
# Endpoint contract
# ---------------------------------------------------------------------


def test_snapshot_endpoint_returns_full_shape(client: TestClient) -> None:
    resp = client.get("/activity/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ok": True,
        "sessions": [],
        "calls": [],
        "high_water_seq": 0,
    }


async def test_snapshot_endpoint_filters_with_since(client: TestClient) -> None:
    await activity.registry().record_session_start(
        session_id="s1", agent_id="orchestrator"
    )
    first = client.get("/activity/snapshot").json()
    assert first["high_water_seq"] >= 1
    next_resp = client.get("/activity/snapshot", params={"since": first["high_water_seq"]})
    body = next_resp.json()
    assert body["sessions"] == []
    assert body["calls"] == []


def test_clear_endpoint_resets_the_registry(client: TestClient) -> None:
    # Seed two sessions.
    import asyncio

    async def seed() -> None:
        reg = activity.registry()
        await reg.record_session_start(session_id="a", agent_id="")
        await reg.record_session_start(session_id="b", agent_id="")

    asyncio.run(seed())
    before = client.get("/activity/snapshot").json()
    assert len(before["sessions"]) == 2
    resp = client.delete("/activity")
    assert resp.status_code == 200
    after = client.get("/activity/snapshot").json()
    assert after["sessions"] == []
    assert after["calls"] == []
