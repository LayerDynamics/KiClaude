"""Activity registry — M3-T-09 source-of-truth for the
`SubagentActivityPanel`.

The Claude Agent SDK runs each subagent in its own session (its own
`session_id`). When a subagent dispatches a tool, the `PreToolUse` /
`PostToolUse` hooks fire on the subagent's session and the SDK
threads the orchestrator's id onto the same payload as
``parent_session_id``. The `SessionStart` hook in
``agent.hooks.lifecycle`` already captures both, including the
``agent_id`` the SDK uses to identify the running agent (which for
sub-sessions is the `AgentDefinition`'s registered name —
"decoupling-auditor", "bom-sourcer", "placement-explorer", or empty
for the orchestrator session).

This module turns those hook emissions into a ring-buffer the
gateway-facing FastAPI surface can serve to the React panel:

- Sessions are keyed by `session_id` and carry the chain back to the
  parent so the UI can render the orchestrator → subagent tree.
- Tool calls are keyed by `tool_use_id` and link back to the session
  they ran in via `session_id`.
- Both kinds are tracked with a monotonically-increasing `seq` so
  the panel can poll incrementally (``GET /activity/sessions?since=N``).

The registry is process-local, the same way `ask_user.AskUserRegistry`
is — kiclaude runs one agent process per project session today. If we
ever shard, swap this for Redis-backed primitives without changing
the public API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# How many entries to retain in the ring buffer per slice (sessions
# and tool calls). The panel only renders the most-recent ~50 anyway;
# 500 leaves headroom for chatty subagents without ballooning RSS.
MAX_ENTRIES = 500


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(slots=True)
class SessionRecord:
    """One agent session — orchestrator or subagent."""

    session_id: str
    agent_id: str
    parent_session_id: str | None
    started_at: str
    ended_at: str | None = None
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "parent_session_id": self.parent_session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "seq": self.seq,
        }


@dataclass(slots=True)
class ToolCallRecord:
    """One tool call within a session. `ok`, `duration_ms`, `ended_at`
    fill in when the matching PostToolUse arrives."""

    tool_use_id: str
    session_id: str
    tool_name: str
    project_id: str | None
    started_at: str
    ended_at: str | None = None
    ok: bool | None = None
    duration_ms: float | None = None
    seq: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """`running` until PostToolUse arrives, then `ok` / `error`."""
        if self.ended_at is None:
            return "running"
        return "ok" if self.ok else "error"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tool_use_id": self.tool_use_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "project_id": self.project_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "seq": self.seq,
        }
        if self.extra:
            out["extra"] = self.extra
        return out


class ActivityRegistry:
    """Append-only registry of session lifecycle + tool-call events.

    Concurrency: every mutator holds an asyncio.Lock so concurrent
    hook fires (multiple subagents in flight) never race. Read paths
    snapshot under the same lock and return plain dicts.
    """

    def __init__(self, *, max_entries: int = MAX_ENTRIES) -> None:
        self._max = max_entries
        self._sessions: dict[str, SessionRecord] = {}
        # Insertion order is preserved (Python dicts ≥3.7) and matches
        # SessionStart firing order, which is what the panel wants.
        self._calls: dict[str, ToolCallRecord] = {}
        self._next_seq = 1
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------
    # Write side — called by the lifecycle hooks.
    # ----------------------------------------------------------------

    async def record_session_start(
        self,
        *,
        session_id: str,
        agent_id: str = "",
        parent_session_id: str | None = None,
    ) -> SessionRecord:
        async with self._lock:
            record = self._sessions.get(session_id)
            seq = self._next_seq
            self._next_seq += 1
            if record is None:
                record = SessionRecord(
                    session_id=session_id,
                    agent_id=agent_id,
                    parent_session_id=parent_session_id,
                    started_at=_iso_now(),
                    seq=seq,
                )
                self._sessions[session_id] = record
                self._enforce_cap(self._sessions)
            else:
                # SessionStart can fire more than once if the SDK
                # resumes a session; refresh agent_id/parent when the
                # new fire carries non-empty values.
                if agent_id and not record.agent_id:
                    record.agent_id = agent_id
                if parent_session_id and not record.parent_session_id:
                    record.parent_session_id = parent_session_id
                record.seq = seq
            return record

    async def record_session_end(self, *, session_id: str) -> SessionRecord | None:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                # SessionEnd without a matching SessionStart — record a
                # synthetic stub so the panel still sees the lifecycle.
                record = SessionRecord(
                    session_id=session_id,
                    agent_id="",
                    parent_session_id=None,
                    started_at=_iso_now(),
                    seq=self._next_seq,
                )
                self._next_seq += 1
                self._sessions[session_id] = record
                self._enforce_cap(self._sessions)
            record.ended_at = _iso_now()
            record.seq = self._next_seq
            self._next_seq += 1
            return record

    async def record_tool_start(
        self,
        *,
        tool_use_id: str,
        session_id: str,
        tool_name: str,
        project_id: str | None,
        parent_session_id: str | None = None,
    ) -> ToolCallRecord:
        async with self._lock:
            # Auto-register the session if PreToolUse fires before
            # SessionStart (can happen for sub-process subagents).
            if session_id and session_id not in self._sessions:
                self._sessions[session_id] = SessionRecord(
                    session_id=session_id,
                    agent_id="",
                    parent_session_id=parent_session_id,
                    started_at=_iso_now(),
                    seq=self._next_seq,
                )
                self._next_seq += 1
                self._enforce_cap(self._sessions)
            elif parent_session_id and session_id in self._sessions:
                # Late-arriving parent linkage — first tool call inside
                # a subagent may be the first signal we have that the
                # session is a child of the orchestrator.
                record = self._sessions[session_id]
                if record.parent_session_id is None:
                    record.parent_session_id = parent_session_id
            call = ToolCallRecord(
                tool_use_id=tool_use_id,
                session_id=session_id,
                tool_name=tool_name,
                project_id=project_id,
                started_at=_iso_now(),
                seq=self._next_seq,
            )
            self._next_seq += 1
            self._calls[tool_use_id] = call
            self._enforce_cap(self._calls)
            return call

    async def record_tool_end(
        self,
        *,
        tool_use_id: str,
        ok: bool,
        duration_ms: float,
    ) -> ToolCallRecord | None:
        async with self._lock:
            call = self._calls.get(tool_use_id)
            if call is None:
                return None
            call.ok = ok
            call.duration_ms = duration_ms
            call.ended_at = _iso_now()
            call.seq = self._next_seq
            self._next_seq += 1
            return call

    # ----------------------------------------------------------------
    # Read side — served by FastAPI to the panel.
    # ----------------------------------------------------------------

    async def snapshot(self, *, since_seq: int | None = None) -> dict[str, Any]:
        """Return the full sessions + calls view, optionally filtered
        to entries with `seq > since_seq`. The panel polls with the
        highest seq it has seen so it only re-renders changed slices."""
        async with self._lock:
            sessions = [
                s.to_dict()
                for s in sorted(self._sessions.values(), key=lambda r: r.seq)
                if since_seq is None or s.seq > since_seq
            ]
            calls = [
                c.to_dict()
                for c in sorted(self._calls.values(), key=lambda r: r.seq)
                if since_seq is None or c.seq > since_seq
            ]
            high_water = self._next_seq - 1
            return {
                "sessions": sessions,
                "calls": calls,
                "high_water_seq": high_water,
            }

    # ----------------------------------------------------------------
    # Maintenance
    # ----------------------------------------------------------------

    def _enforce_cap(self, store: dict[str, Any]) -> None:
        if len(store) <= self._max:
            return
        # Drop oldest by insertion order. Python dicts preserve insert
        # order so popitem(last=False) is the right primitive.
        drop = len(store) - self._max
        for key in list(store.keys())[:drop]:
            store.pop(key, None)


_REGISTRY = ActivityRegistry()


def registry() -> ActivityRegistry:
    """Process-wide accessor for the singleton registry."""
    return _REGISTRY


def reset_registry_for_tests(*, max_entries: int = MAX_ENTRIES) -> None:
    """Replace the singleton with a fresh empty registry. Test
    fixtures call this so prior-run leakage doesn't contaminate."""
    global _REGISTRY
    _REGISTRY = ActivityRegistry(max_entries=max_entries)


__all__ = [
    "MAX_ENTRIES",
    "ActivityRegistry",
    "SessionRecord",
    "ToolCallRecord",
    "registry",
    "reset_registry_for_tests",
]
