"""Claude Agent SDK lifecycle hooks for kiclaude.

Implements PreToolUse + PostToolUse JSONL emitters that satisfy the
M0-Q-05 OTel contract: every emitted line carries
`{ts, tool_name, session_id, project_id, duration_ms (post only),
ok (post only), event}`.

The hooks are pure callables — no I/O beyond writing one line to the
sink (stdout by default). Tests inject a `BytesIO`-style sink and
assert the emitted JSONL shape.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent import activity
from agent.telemetry import tracer

# In-process map: tool_use_id → (started_at_monotonic, project_id) so
# PostToolUse can compute duration_ms.
_INFLIGHT: dict[str, tuple[float, str | None]] = {}


def _parent_session_id(input_data: dict[str, Any]) -> str | None:
    """Subagent flag — the Claude Agent SDK forwards
    ``parent_session_id`` on input_data when the current hook runs
    inside a delegated subagent. Returns the value if present and
    non-empty, otherwise None."""
    raw = input_data.get("parent_session_id")
    if isinstance(raw, str) and raw:
        return raw
    return None


def _set_common_attrs(
    span: Any, *, hook_event: str, input_data: dict[str, Any], project_id: str | None
) -> None:
    """Attach the M1-Q-04 attribute set every agent span must carry."""
    span.set_attribute("hook_event", hook_event)
    span.set_attribute("session_id", input_data.get("session_id", "") or "")
    parent = _parent_session_id(input_data)
    if parent:
        span.set_attribute("parent_session_id", parent)
    if project_id:
        span.set_attribute("project_id", project_id)


def _now_iso() -> str:
    """UTC ISO-8601 timestamp with millisecond precision."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _project_id_from_env_or_input(tool_input: dict[str, Any]) -> str | None:
    """Resolve the project_id for this hook call.

    Resolution order:

    1. Explicit `project_id` arg on the tool call (declarative tools
       pass this in their schema).
    2. `KICLAUDE_PROJECT_ID` env var — the kiserver process exports
       this for any subprocess it spawns.
    3. `None` — older calls before the project_id contract was added.
    """
    explicit = tool_input.get("project_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    env_id = os.environ.get("KICLAUDE_PROJECT_ID")
    return env_id if env_id else None


@dataclass(slots=True)
class HookSink:
    """Where hook JSONL lines go. Defaults to stdout; tests inject a
    `StringIO` to capture and assert."""

    stream: Any = field(default_factory=lambda: sys.stdout)

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, sort_keys=True, separators=(",", ":"))
        self.stream.write(line + "\n")
        flush = getattr(self.stream, "flush", None)
        if callable(flush):
            flush()


# Module-level sink used by the live hooks. Tests reassign this.
SINK = HookSink()


async def pre_tool_use(
    input_data: dict[str, Any],
    _tool_use_id: str | None,
    _context: Any,
) -> dict[str, Any]:
    """`PreToolUse` callback. Logs the attempt and stashes a start
    timestamp keyed by `tool_use_id` for `post_tool_use` to find."""
    tool_use_id = input_data.get("tool_use_id", _tool_use_id or "")
    project_id = _project_id_from_env_or_input(input_data.get("tool_input", {}))
    tool_name = input_data.get("tool_name", "")
    with tracer.start_as_current_span("agent.hook.pre_tool_use") as span:
        _set_common_attrs(
            span,
            hook_event="PreToolUse",
            input_data=input_data,
            project_id=project_id,
        )
        span.set_attribute("tool_name", tool_name)
        span.set_attribute("tool_use_id", tool_use_id)
        _INFLIGHT[tool_use_id] = (time.monotonic(), project_id)
        SINK.write(
            {
                "event": "PreToolUse",
                "ts": _now_iso(),
                "tool_name": tool_name,
                "session_id": input_data.get("session_id", ""),
                "tool_use_id": tool_use_id,
                "project_id": project_id,
            }
        )
    # M3-T-09: feed the live SubagentActivityPanel registry alongside
    # the JSONL sink. Late tool_use_ids without a SessionStart get a
    # synthetic session record so the panel still shows the call.
    await activity.registry().record_tool_start(
        tool_use_id=tool_use_id,
        session_id=input_data.get("session_id", "") or "",
        tool_name=tool_name,
        project_id=project_id,
        parent_session_id=_parent_session_id(input_data),
    )
    # Empty dict = "no decision, proceed normally" per SDK contract.
    return {}


async def post_tool_use(
    input_data: dict[str, Any],
    _tool_use_id: str | None,
    _context: Any,
) -> dict[str, Any]:
    """`PostToolUse` callback. Pops the start timestamp, computes
    duration_ms, and emits the structured event."""
    tool_use_id = input_data.get("tool_use_id", _tool_use_id or "")
    started_at, project_id = _INFLIGHT.pop(tool_use_id, (time.monotonic(), None))
    duration_ms = round((time.monotonic() - started_at) * 1000.0, 3)
    tool_response = input_data.get("tool_response", {})
    ok = _result_ok(tool_response)
    tool_name = input_data.get("tool_name", "")
    with tracer.start_as_current_span("agent.hook.post_tool_use") as span:
        _set_common_attrs(
            span,
            hook_event="PostToolUse",
            input_data=input_data,
            project_id=project_id,
        )
        span.set_attribute("tool_name", tool_name)
        span.set_attribute("tool_use_id", tool_use_id)
        span.set_attribute("duration_ms", duration_ms)
        span.set_attribute("ok", ok)
        SINK.write(
            {
                "event": "PostToolUse",
                "ts": _now_iso(),
                "tool_name": tool_name,
                "session_id": input_data.get("session_id", ""),
                "tool_use_id": tool_use_id,
                "project_id": project_id,
                "duration_ms": duration_ms,
                "ok": ok,
            }
        )
    await activity.registry().record_tool_end(
        tool_use_id=tool_use_id,
        ok=ok,
        duration_ms=duration_ms,
    )
    return {}


async def session_start(
    input_data: dict[str, Any],
    _tool_use_id: str | None,
    _context: Any,
) -> dict[str, Any]:
    """`SessionStart` callback."""
    with tracer.start_as_current_span("agent.hook.session_start") as span:
        _set_common_attrs(
            span,
            hook_event="SessionStart",
            input_data=input_data,
            project_id=_project_id_from_env_or_input(input_data),
        )
        span.set_attribute("cwd", input_data.get("cwd", "") or "")
        span.set_attribute("agent_id", input_data.get("agent_id", "") or "")
        SINK.write(
            {
                "event": "SessionStart",
                "ts": _now_iso(),
                "session_id": input_data.get("session_id", ""),
                "cwd": input_data.get("cwd", ""),
                "agent_id": input_data.get("agent_id", ""),
            }
        )
    await activity.registry().record_session_start(
        session_id=input_data.get("session_id", "") or "",
        agent_id=input_data.get("agent_id", "") or "",
        parent_session_id=_parent_session_id(input_data),
    )
    return {}


async def session_end(
    input_data: dict[str, Any],
    _tool_use_id: str | None,
    _context: Any,
) -> dict[str, Any]:
    """`SessionEnd` (Stop) callback."""
    with tracer.start_as_current_span("agent.hook.session_end") as span:
        _set_common_attrs(
            span,
            hook_event="SessionEnd",
            input_data=input_data,
            project_id=_project_id_from_env_or_input(input_data),
        )
        SINK.write(
            {
                "event": "SessionEnd",
                "ts": _now_iso(),
                "session_id": input_data.get("session_id", ""),
            }
        )
    await activity.registry().record_session_end(
        session_id=input_data.get("session_id", "") or "",
    )
    return {}


def _result_ok(response: Any) -> bool:
    """Heuristic for "the tool succeeded". MCP tools return a dict
    with `content` and optionally `isError`; anything raising would
    short-circuit and never reach `PostToolUse` (that's why
    PostToolUseFailure exists as a separate event)."""
    if isinstance(response, dict):
        if response.get("isError"):
            return False
        structured = response.get("structured")
        if isinstance(structured, dict) and "ok" in structured:
            return bool(structured["ok"])
    return True


def reset_inflight_for_tests() -> None:
    """Test helper — clear the inflight map between cases."""
    _INFLIGHT.clear()


__all__ = [
    "SINK",
    "HookSink",
    "post_tool_use",
    "pre_tool_use",
    "reset_inflight_for_tests",
    "session_end",
    "session_start",
]
