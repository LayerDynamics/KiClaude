"""kiclaude agent FastAPI app — port :8082.

Endpoints (M0-P-02):

- `GET /health` → `{ok: true, service: "agent", version: <semver>}`.
- `POST /echo` body `{prompt}` → runs the prompt through
  [`claude_agent_sdk.query()`][claude_agent_sdk.query] and returns the
  collected assistant text. Requires SOME form of Claude credential
  reachable — `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
  `CLAUDE_CODE_OAUTH_TOKEN`, Bedrock/Vertex opt-in, OR a keychain
  credential from `claude login`. See [`agent.auth.is_available`][]
  for the full probe. Returns 503 listing every accepted path
  otherwise.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent import __version__, activity, ask_user, auth

log = structlog.get_logger(__name__)

app = FastAPI(
    title="kiclaude-agent",
    version=__version__,
    description="Claude Agent SDK driver for kiclaude.",
)


class EchoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4_000)
    # Inline future-proof handle for the project the prompt is about;
    # the M0 /echo endpoint doesn't use it, but downstream M0-P-06
    # session wiring will key its kc_mcp project_id on it.
    project_id: str | None = Field(default=None, max_length=128)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe. Returns a stable JSON envelope so the gateway's
    `/api/health` aggregator (M0-T-01) can fan out across services."""
    return {"ok": True, "service": "agent", "version": __version__}


@app.post("/echo")
async def echo(req: EchoRequest) -> JSONResponse:
    """One-shot Claude round-trip via `claude_agent_sdk.query()`.

    Streams the assistant's reply, collects the concatenated text, and
    returns it as `{ok, reply, project_id}`. Mirrors the contract that
    M0-Q-03's Playwright smoke uses to verify chat reaches the model.
    """
    auth_result = auth.is_available()
    if not auth_result.ok:
        raise HTTPException(
            status_code=503,
            detail=auth.describe_unavailable(),
        )

    # Imported lazily so the rest of the app boots even without the
    # SDK installed (development convenience; CI requires it).
    from claude_agent_sdk import (  # type: ignore[import-not-found]
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(setting_sources=None)
    reply_parts: list[str] = []
    async for msg in query(prompt=req.prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    reply_parts.append(block.text)
    reply = "".join(reply_parts)
    log.info("echo_replied", project_id=req.project_id, chars=len(reply))
    return JSONResponse(
        {
            "ok": True,
            "reply": reply,
            "project_id": req.project_id,
            "auth_source": auth_result.source,
        }
    )


@app.get("/auth/status")
async def auth_status() -> dict[str, Any]:
    """Diagnostic — surface which auth path (if any) the service
    will use when `/echo` runs. Reports source + human-readable
    detail. Useful for the M2-Q-02 e2e setup, which can decide
    whether to start the agent at all based on this probe."""
    auth.reset_cache()
    result = auth.is_available()
    return {
        "ok": result.ok,
        "source": result.source,
        "detail": result.detail,
    }


# ---------------------------------------------------------------------
# M3-P-08 — AskUserQuestion bridge endpoints.
#
# Three endpoints the WebSocket gateway uses to shuttle questions
# between the agent (`await ask_user.dispatch_question(...)`) and
# the React chat sidebar (which renders `AskUserQuestionCard` and
# POSTs the user's pick back).
# ---------------------------------------------------------------------


class AskUserAnswerBody(BaseModel):
    """Body for `POST /ask-user/{question_id}/answer`."""

    picks: list[str] = Field(default_factory=list, max_length=8)
    notes: str = Field(default="", max_length=4_000)


@app.get("/ask-user/pending")
async def ask_user_pending() -> dict[str, Any]:
    """Return every still-unanswered question's payload. The
    WebSocket gateway polls this on each accepted connection and
    forwards every entry as one `ask_user_question` frame to the
    React side."""
    questions = await ask_user.registry().pending()
    return {"ok": True, "questions": questions}


@app.post("/ask-user/{question_id}/answer")
async def ask_user_answer(question_id: str, body: AskUserAnswerBody) -> dict[str, Any]:
    """Resolve the awaiting agent coroutine with the user's pick.
    Returns `{ok: false, error: ...}` when the question id is
    unknown — e.g. the caller cancelled before the answer landed."""
    answer = ask_user.AskUserAnswer(picks=body.picks, notes=body.notes)
    ok = await ask_user.registry().answer(question_id, answer)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"no pending question with id {question_id!r}",
        )
    log.info(
        "ask_user_answered",
        question_id=question_id,
        picks=len(body.picks),
        notes_chars=len(body.notes),
    )
    return {"ok": True}


@app.delete("/ask-user/{question_id}")
async def ask_user_cancel(question_id: str) -> dict[str, Any]:
    """Cancel a pending question — used by the gateway when the
    WebSocket drops before the user answers, so the agent coroutine
    receives a CancelledError instead of waiting forever."""
    ok = await ask_user.registry().cancel(question_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"no pending question with id {question_id!r}",
        )
    log.info("ask_user_cancelled", question_id=question_id)
    return {"ok": True}


# ----------------------------------------------------------------
# M3-T-09 — Subagent activity panel data source.
#
# The React `SubagentActivityPanel` polls `/activity/snapshot` and
# passes back the highest `seq` it has seen as `since`; the registry
# returns only entries with `seq > since`, so the panel re-renders
# only the changed rows on each tick. A 1000 ms cadence is more than
# enough for human-eye refresh and keeps the gateway round-trip cheap
# even for chatty subagents.
# ----------------------------------------------------------------


@app.get("/activity/snapshot")
async def activity_snapshot(since: int | None = None) -> dict[str, Any]:
    """Return all session + tool-call records with `seq > since`. The
    panel uses the response's `high_water_seq` as next round's
    `since` parameter."""
    snap = await activity.registry().snapshot(since_seq=since)
    return {"ok": True, **snap}


@app.delete("/activity")
async def activity_clear() -> dict[str, Any]:
    """Reset the registry — used by tests and (rarely) the panel's
    "Clear" button. Production never calls this; the ring buffer
    rolls itself over."""
    activity.reset_registry_for_tests()
    return {"ok": True}


__all__ = ["app"]
