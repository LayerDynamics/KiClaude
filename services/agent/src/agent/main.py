"""kiclaude agent FastAPI app — port :8082.

Endpoints (M0-P-02):

- `GET /health` → `{ok: true, service: "agent", version: <semver>}`.
- `POST /echo` body `{prompt}` → runs the prompt through
  [`claude_agent_sdk.query()`][claude_agent_sdk.query] and returns the
  collected assistant text. Requires `ANTHROPIC_API_KEY` in the env;
  returns 503 otherwise. M0-P-06 will replace this with a full
  ClaudeSDKClient session that wires in kc_mcp + hooks.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent import __version__

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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not set; /echo is disabled.",
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
        }
    )


__all__ = ["app"]
