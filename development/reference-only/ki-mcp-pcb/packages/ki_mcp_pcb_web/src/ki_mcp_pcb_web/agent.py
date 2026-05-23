"""Claude Agent SDK integration for the GUI co-pilot (SPEC-1 §6.5).

``AgentSession`` wraps ``claude_agent_sdk.ClaudeSDKClient`` — one session
per WebSocket connection. The agent is configured with the ki-mcp-pcb MCP
server as its toolset, so Claude inside the GUI has the same tools as
Claude Code in a terminal, and with ``cwd`` pinned to the GUI working
directory (SPEC-1 NFR-7).

The Claude Agent SDK is an optional dependency (``ki-mcp-pcb-web[agent]``)
imported lazily — the pipeline-only GUI runs fine without it.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

#: The approval-gate callback the WebSocket layer supplies (wired in G2-T3):
#: ``(tool_name, tool_input, context) -> PermissionResult``.
ToolPermissionCallback = Callable[[str, dict[str, Any], Any], Awaitable[Any]]


class AgentUnavailableError(RuntimeError):
    """The Claude Agent SDK is not installed (the ``agent`` extra is absent)."""


def agent_available() -> bool:
    """Return ``True`` when the Claude Agent SDK can be imported."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


#: The MCP fab-export tool, under its bare name and the namespaced form the
#: agent sees it as (``mcp__<server>__<tool>``).
_FAB_EXPORT_TOOLS = frozenset({
    "tool_export_fab",
    "mcp__ki-mcp-pcb__tool_export_fab",
})

#: Built-in file-mutating tools — gated only when they target the CIR file.
_FILE_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def is_cir_write(
    tool_name: str, tool_input: dict[str, Any], *, cir_filename: str
) -> bool:
    """Return ``True`` when this tool call may write the working CIR file.

    Two cases, both used by the approval gate *and* to fire the
    ``cir_changed`` GUI refresh:

    * a built-in file-mutating tool whose target path basename is the CIR
      filename, and
    * a ``Bash`` command whose text references the CIR filename — the only
      way ``sed -i`` / ``> board.cir.yaml`` could otherwise slip past the
      gate. The match is deliberately coarse: a read-only ``cat`` of the CIR
      costs one extra approval click, never a missed write (SPEC-1 FR-16).
    """
    if tool_name in _FILE_WRITE_TOOLS:
        raw_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return bool(raw_path) and Path(str(raw_path)).name == cir_filename
    if tool_name == "Bash":
        return cir_filename in str(tool_input.get("command", ""))
    return False


def approval_reason(
    tool_name: str, tool_input: dict[str, Any], *, cir_filename: str
) -> str | None:
    """Return why a tool call needs user approval, or ``None`` to auto-allow.

    The approval gate (SPEC-1 FR-16) covers the irreversible / outward-facing
    actions: exporting a fab package, and any write to the working CIR file —
    which is also the only path by which a ``Board.signoff.*`` flag could be
    flipped, so gating CIR writes gates sign-off too.
    """
    if tool_name in _FAB_EXPORT_TOOLS:
        return "exports a manufacturing (fab) package"
    if is_cir_write(tool_name, tool_input, cir_filename=cir_filename):
        return f"writes the working CIR file ({cir_filename})"
    return None


def _system_prompt(working_dir: Path) -> str:
    """The agent's system prompt — the CIR contract and the house rules."""
    return (
        "You are the ki-mcp-pcb co-pilot, embedded in its browser GUI. "
        "ki-mcp-pcb turns plain-text circuit descriptions into manufacturable "
        "KiCad PCBs.\n\n"
        f"The working directory is {working_dir}. The working CIR file is "
        "'board.cir.yaml' — the typed Pydantic CIR is the contract; every "
        "transformation goes natural-language -> CIR -> KiCad.\n\n"
        "Use the ki-mcp-pcb MCP tools to validate, synthesize, build, route "
        "and check the board. You may read and edit board.cir.yaml. You must "
        "NOT set any Board.signoff.* flag yourself — sign-off is a human "
        "decision the user makes in the GUI."
    )


def _mcp_servers() -> dict[str, Any]:
    """The MCP-server config wiring in ki_mcp_pcb_server as the agent's tools.

    Launched over stdio with the current interpreter so it resolves the
    same workspace environment the backend runs in.
    """
    return {
        "ki-mcp-pcb": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-c", "from ki_mcp_pcb_server.server import main; main()"],
        }
    }


def _normalize(message: Any) -> list[dict[str, Any]]:
    """Translate one Claude Agent SDK message into GUI event dicts.

    Assistant text and tool-use blocks, tool results carried back on user
    messages, and the terminal result message all become flat JSON the
    WebSocket layer forwards verbatim.
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, UserMessage
    from claude_agent_sdk.types import (
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    events: list[dict[str, Any]] = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                events.append({"type": "text", "text": block.text})
            elif isinstance(block, ThinkingBlock):
                events.append({"type": "thinking", "text": block.thinking})
            elif isinstance(block, ToolUseBlock):
                events.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                events.append({
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": bool(block.is_error),
                })
    elif isinstance(message, ResultMessage):
        events.append({
            "type": "done",
            "is_error": message.is_error,
            "result": message.result,
            "cost_usd": message.total_cost_usd,
        })
    return events


class AgentSession:
    """A live Claude conversation backing one GUI chat WebSocket.

    Construct, ``connect()``, then ``send(prompt)`` per turn; the session
    stays connected across turns. ``aclose()`` shuts it down.
    """

    def __init__(
        self,
        working_dir: Path,
        *,
        can_use_tool: ToolPermissionCallback | None = None,
        model: str | None = None,
    ) -> None:
        if not agent_available():
            raise AgentUnavailableError(
                "the Claude Agent SDK is not installed — "
                "install ki-mcp-pcb-web[agent] to use the co-pilot"
            )
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        self._options = ClaudeAgentOptions(
            system_prompt=_system_prompt(working_dir),
            mcp_servers=_mcp_servers(),
            cwd=str(working_dir),
            permission_mode="default",
            can_use_tool=can_use_tool,
            model=model,
        )
        self._client = ClaudeSDKClient(options=self._options)
        self._connected = False

    async def connect(self) -> None:
        """Open the agent connection."""
        await self._client.connect()
        self._connected = True

    async def aclose(self) -> None:
        """Close the agent connection if open."""
        if self._connected:
            await self._client.disconnect()
            self._connected = False

    async def send(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        """Send one user turn; yield the agent's events until it completes."""
        await self._client.query(prompt)
        async for message in self._client.receive_response():
            for event in _normalize(message):
                yield event
