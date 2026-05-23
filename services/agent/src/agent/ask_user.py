"""AskUserQuestion bridge — M3-P-08 agent-side dispatcher.

The React chat sidebar already handles `ask_user_question` WebSocket
frames and emits `ask_user_question_answer` replies (wired in
M1-T-07; see `client/src/components/chat/ChatSidebar.tsx` +
`AskUserQuestionCard.tsx`). What was missing — and what this module
provides — is the agent-side half: a Future-keyed registry the
orchestrator can call as `await dispatch_question(...)` to surface a
question to the user and block until they answer.

## Architecture

The agent emits questions and consumes answers via three FastAPI
endpoints on `services/agent/` (see [`agent.main`]). The WebSocket
gateway in `services/server` proxies between the React WS frames and
these HTTP endpoints:

```text
React UI ──ask_user_question──▶ ChatSidebar ──pick──▶ POST /ask-user/{id}/answer
                                                              │
                                                              ▼
agent code ──await dispatch_question(...)──▶ Future.set_result(answer)
                                              ▲
                                              │
                          POST /ask-user/pending  (gateway polls,
                          returns oldest unanswered question)
```

`dispatch_question(...)` is `async` and returns the user's answer
when it lands. If the user closes the chat without answering, the
caller can apply `asyncio.wait_for(..., timeout=N)` to cap the
blocking window.

## Why not a hook?

`claude_agent_sdk.AgentDefinition` supports a `permissionMode` and
`can_use_tool` but doesn't expose a hook surface for the built-in
`AskUserQuestion` SDK tool — that tool's normal behaviour is to
prompt on stdin, which the agent service's process has no human at.
The cleanest bridge is therefore a REGISTRY the agent code (the
orchestrator or any custom MCP tool) can call directly via
`dispatch_question(...)`. Skills and commands that want to surface
a question wire it explicitly rather than relying on Claude's
built-in tool.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AskUserQuestionPayload:
    """One question pending an answer. Mirrors the
    `AskUserQuestion` interface on the React side
    (`AskUserQuestionCard.tsx`)."""

    id: str
    question: str
    options: list[dict[str, str]]
    header: str | None = None
    multi_select: bool = False
    project_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "multiSelect": self.multi_select,
        }
        if self.header is not None:
            out["header"] = self.header
        if self.project_id is not None:
            out["projectId"] = self.project_id
        return out


@dataclass(frozen=True)
class AskUserAnswer:
    """One answered question — the shape the React side posts to
    `POST /ask-user/{id}/answer`."""

    picks: list[str]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"picks": list(self.picks), "notes": self.notes}


@dataclass
class _PendingEntry:
    payload: AskUserQuestionPayload
    future: asyncio.Future[AskUserAnswer]
    enqueued_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())


class AskUserRegistry:
    """Process-local registry of pending questions keyed by id.

    `dispatch_question(...)` enqueues a question and returns an
    awaitable for the answer. The gateway polls `pending()` to find
    questions to forward to the WebSocket. Once the user answers,
    the gateway POSTs to `answer(id, ...)` and the awaiter unblocks.

    Single-process by design — kiclaude's agent service runs one
    Python process per project session. If we ever fan out to a
    worker pool, replace this with Redis-backed primitives.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingEntry] = {}
        self._lock = asyncio.Lock()

    async def dispatch_question(
        self,
        question: str,
        options: list[dict[str, str]],
        *,
        header: str | None = None,
        multi_select: bool = False,
        project_id: str | None = None,
    ) -> AskUserAnswer:
        """Enqueue a question for the UI and `await` the user's answer.

        Raises `ValueError` if `options` is empty or contains
        entries without `label`. Raises `asyncio.CancelledError` if
        the caller cancels (the entry stays in the registry so a
        late answer is silently discarded rather than crashing the
        FastAPI handler).
        """
        if not options:
            raise ValueError("AskUserQuestion needs at least one option")
        for o in options:
            if "label" not in o or not o["label"]:
                raise ValueError("every option must have a non-empty `label`")
        loop = asyncio.get_event_loop()
        question_id = uuid.uuid4().hex
        payload = AskUserQuestionPayload(
            id=question_id,
            question=question,
            options=options,
            header=header,
            multi_select=multi_select,
            project_id=project_id,
        )
        future: asyncio.Future[AskUserAnswer] = loop.create_future()
        async with self._lock:
            self._pending[question_id] = _PendingEntry(payload=payload, future=future)
        try:
            return await future
        finally:
            # Whether the future resolved or the caller cancelled, the
            # registry entry stops being addressable. A late answer
            # falls into the no-such-id path in `answer()` and is
            # quietly dropped.
            async with self._lock:
                self._pending.pop(question_id, None)

    async def pending(self) -> list[dict[str, Any]]:
        """Return every still-unanswered question's payload, oldest
        first. The gateway uses this to forward the question to the
        active WebSocket connection — typically the response is one
        question, but a multi-tab chat can have multiple."""
        async with self._lock:
            entries = sorted(
                self._pending.values(), key=lambda e: e.enqueued_at
            )
        return [e.payload.to_dict() for e in entries]

    async def answer(self, question_id: str, answer: AskUserAnswer) -> bool:
        """Resolve the awaiter for `question_id`. Returns True on
        success, False if there is no such pending question (e.g.
        the caller cancelled before the answer arrived)."""
        async with self._lock:
            entry = self._pending.get(question_id)
        if entry is None:
            return False
        if not entry.future.done():
            entry.future.set_result(answer)
        return True

    async def cancel(self, question_id: str) -> bool:
        """Cancel a specific pending question — the awaiter receives
        a CancelledError. Used by the gateway when the WebSocket
        connection drops before the user answers."""
        async with self._lock:
            entry = self._pending.get(question_id)
        if entry is None:
            return False
        if not entry.future.done():
            entry.future.cancel()
        return True

    def has_pending(self, question_id: str) -> bool:
        """Synchronous probe — used by tests + diagnostic endpoints
        that don't want to take the lock."""
        return question_id in self._pending


# Module-level singleton — the agent service holds one registry per
# process. The FastAPI handlers in `agent.main` reach for this via
# the `registry()` accessor below.
_REGISTRY = AskUserRegistry()


def registry() -> AskUserRegistry:
    """Process-wide accessor for the singleton registry."""
    return _REGISTRY


def reset_registry_for_tests() -> None:
    """Replace the singleton with a fresh empty registry. Tests
    that exercise `dispatch_question` call this in their fixtures so
    leaked Futures from prior tests don't pollute the queue."""
    global _REGISTRY
    _REGISTRY = AskUserRegistry()


__all__ = [
    "AskUserAnswer",
    "AskUserQuestionPayload",
    "AskUserRegistry",
    "dispatch_question",
    "registry",
    "reset_registry_for_tests",
]


async def dispatch_question(
    question: str,
    options: list[dict[str, str]],
    *,
    header: str | None = None,
    multi_select: bool = False,
    project_id: str | None = None,
) -> AskUserAnswer:
    """Module-level convenience — most callers want the singleton."""
    return await _REGISTRY.dispatch_question(
        question,
        options,
        header=header,
        multi_select=multi_select,
        project_id=project_id,
    )
