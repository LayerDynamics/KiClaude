"""Build the [`ClaudeAgentOptions`][claude_agent_sdk.ClaudeAgentOptions]
that wires every piece together: kiclaude MCP server, lifecycle hooks,
and the project-local `.claude/` settings directory.

[`build_options()`][build_options] is the M0-P-06 acceptance surface —
its returned options object is what the agent session passes to
[`ClaudeSDKClient`][claude_agent_sdk.ClaudeSDKClient].
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    SettingSource,  # `Literal["user", "project", "local"]`
)

from agent.hooks import (
    permission_hook,
    post_tool_use,
    pre_tool_use,
    session_end,
    session_start,
)
from agent.subagents import all_subagents


def _kc_mcp_config() -> dict[str, Any]:
    """Build the kiclaude MCP server config from `kc_mcp.server`.

    Lifted into its own helper so tests can replace it with a stub
    without importing the full kc_mcp module (which imports the
    Claude Agent SDK transitively — fine, but slow under repeated
    test discovery)."""
    from kc_mcp.server import build_server

    return {"kiclaude": build_server()}


def build_options(
    *,
    setting_sources: list[SettingSource] | None = None,
    extra_mcp_servers: dict[str, Any] | None = None,
) -> ClaudeAgentOptions:
    """Return a ready-to-use `ClaudeAgentOptions` for the agent service.

    Args:
        setting_sources: Override the default settings discovery.
            Defaults to `["project"]` so the on-disk
            `.claude/settings.json` is honored.
        extra_mcp_servers: Additional MCP servers to merge alongside
            the kiclaude one. Each entry's key becomes the server
            name; the value is its config dict.

    Returns:
        A configured `ClaudeAgentOptions` instance.
    """
    mcp_servers = _kc_mcp_config()
    if extra_mcp_servers:
        mcp_servers.update(extra_mcp_servers)

    hooks: dict[str, list[HookMatcher]] = {
        # M1-P-06 wires the permission gate alongside the JSONL
        # emitter. PreToolUse handlers run in registration order; the
        # logger fires first so even denied calls show up in the
        # activity stream, then the gate decides allow/deny/ask.
        "PreToolUse": [
            HookMatcher(hooks=[pre_tool_use]),
            HookMatcher(hooks=[permission_hook]),
        ],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
        "SessionStart": [HookMatcher(hooks=[session_start])],
        "Stop": [HookMatcher(hooks=[session_end])],
    }

    return ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        hooks=hooks,
        setting_sources=setting_sources or ["project"],
        cwd=str(_project_root()),
        # M3-C-08: register the three M3-P-07 subagents so /pcb-review
        # and /explore-placements can spawn them through the SDK's
        # native dispatch surface.
        agents=all_subagents(),
    )


def _project_root() -> Path:
    """Walk up from this file to find the kiclaude repo root (the dir
    containing `pyproject.toml` + `.claude/`)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".claude").is_dir() and (parent / "pyproject.toml").exists():
            return parent
    # Fallback: cwd. Acceptable for non-standard test layouts.
    return Path.cwd()


__all__ = ["build_options"]
