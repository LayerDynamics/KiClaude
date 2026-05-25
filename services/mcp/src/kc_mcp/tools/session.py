"""`kc_session_fork` — branch a chat session for "what-if" exploration
(SPEC §A.2.1, §8.4).

Forking writes a new session manifest under the project's
`.kiclaude/sessions/` store that records `forked_from` the parent. The
agent's session layer (M1-P-07) reads that store, so the fork shows up
in the session-tree picker and can be resumed as an independent branch.

MCP tools are stateless (first principle #6), so the on-disk write is
delegated to kiserver (which owns the project path) via
`POST /project/{id}/session/fork`. Note: the SPEC sketch shows
`{session_id, label?}`; a `project_id` is required in practice because
the session store is per-project, and Claude always has it in context.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_post


@tool(
    "kc_session_fork",
    "Fork a chat session into an independent 'what-if' branch. Writes a "
    "new session manifest recording `forked_from` the parent so the UI "
    "session-tree can show and resume it. Returns {ok, new_session_id, "
    "parent_session_id}.",
    {"project_id": str, "session_id": str, "label": str},
)
async def kc_session_fork(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    parent = (args.get("session_id") or "").strip()
    if not project_id or not parent:
        return error_envelope("`project_id` and `session_id` (the parent) are required")
    label = (args.get("label") or "").strip()
    try:
        result = await kiserver_post(
            f"/project/{project_id}/session/fork",
            {"parent_session_id": parent, "label": label},
        )
    except Exception as e:
        return error_envelope(f"kiserver session fork failed: {e}", project_id=project_id)
    if not result.get("ok"):
        return error_envelope(
            result.get("detail") or "session fork failed", project_id=project_id
        )
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "parent_session_id": parent,
            "new_session_id": result.get("new_session_id"),
            "label": label,
        }
    )


__all__ = ["kc_session_fork"]
