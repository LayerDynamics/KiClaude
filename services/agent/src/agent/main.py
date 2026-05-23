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

from agent import __version__, auth

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


__all__ = ["app"]
