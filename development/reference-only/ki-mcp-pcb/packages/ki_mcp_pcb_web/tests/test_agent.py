"""Unit tests for the Claude Agent SDK integration module (SPEC-1 G2-T1).

The Claude Agent SDK is installed in the dev group, so the dataclasses are
real — but no live Anthropic call is ever made: ``AgentSession`` is exercised
against a stub ``ClaudeSDKClient`` injected via monkeypatch (SPEC-1 §8).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("claude_agent_sdk")
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from ki_mcp_pcb_web import agent


# --------------------------------------------------------------------------
# agent_available / _system_prompt / _mcp_servers
# --------------------------------------------------------------------------
def test_agent_available_true_when_sdk_installed() -> None:
    assert agent.agent_available() is True


def test_system_prompt_states_the_contract_and_working_dir() -> None:
    prompt = agent._system_prompt(Path("/work/board"))
    assert "/work/board" in prompt
    assert "board.cir.yaml" in prompt
    assert "CIR" in prompt
    # The house rule: the agent must not flip sign-off flags itself.
    assert "signoff" in prompt.lower()


def test_mcp_servers_wires_the_ki_mcp_pcb_server_over_stdio() -> None:
    servers = agent._mcp_servers()
    assert set(servers) == {"ki-mcp-pcb"}
    cfg = servers["ki-mcp-pcb"]
    assert cfg["type"] == "stdio"
    assert cfg["command"] == sys.executable
    assert cfg["args"][0] == "-c"
    assert "ki_mcp_pcb_server.server" in cfg["args"][1]


# --------------------------------------------------------------------------
# approval_reason — the FR-16 approval-gate policy
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool_name",
    ["tool_export_fab", "mcp__ki-mcp-pcb__tool_export_fab"],
)
def test_approval_reason_gates_fab_export(tool_name: str) -> None:
    reason = agent.approval_reason(tool_name, {}, cir_filename="board.cir.yaml")
    assert reason is not None
    assert "fab" in reason.lower()


@pytest.mark.parametrize("tool_name", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_approval_reason_gates_a_cir_file_write(tool_name: str) -> None:
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    reason = agent.approval_reason(
        tool_name,
        {key: "/work/board/board.cir.yaml"},
        cir_filename="board.cir.yaml",
    )
    assert reason is not None
    assert "board.cir.yaml" in reason


def test_approval_reason_allows_a_write_to_a_non_cir_file() -> None:
    reason = agent.approval_reason(
        "Write", {"file_path": "/work/board/notes.txt"}, cir_filename="board.cir.yaml"
    )
    assert reason is None


def test_approval_reason_gates_a_bash_write_to_the_cir() -> None:
    reason = agent.approval_reason(
        "Bash",
        {"command": "printf 'cir_version: 0.2' > board.cir.yaml"},
        cir_filename="board.cir.yaml",
    )
    assert reason is not None
    assert "board.cir.yaml" in reason


def test_approval_reason_allows_read_only_tools() -> None:
    for tool_name in ("tool_validate_cir", "Read", "tool_drc", "Grep"):
        assert (
            agent.approval_reason(tool_name, {}, cir_filename="board.cir.yaml") is None
        )
    # A Bash command that does not name the CIR file is auto-allowed.
    assert (
        agent.approval_reason(
            "Bash", {"command": "kicad-cli version"}, cir_filename="board.cir.yaml"
        )
        is None
    )


# --------------------------------------------------------------------------
# is_cir_write — the cir_changed-refresh detector (G2-T6)
# --------------------------------------------------------------------------
def test_is_cir_write_true_for_a_write_to_the_cir_file() -> None:
    assert agent.is_cir_write(
        "Write",
        {"file_path": "/work/board/board.cir.yaml"},
        cir_filename="board.cir.yaml",
    )
    assert agent.is_cir_write(
        "NotebookEdit",
        {"notebook_path": "board.cir.yaml"},
        cir_filename="board.cir.yaml",
    )


def test_is_cir_write_false_for_other_files_and_tools() -> None:
    # A write, but to a different file.
    assert not agent.is_cir_write(
        "Write", {"file_path": "/work/notes.md"}, cir_filename="board.cir.yaml"
    )
    # The CIR path, but a read-only tool.
    assert not agent.is_cir_write(
        "Read", {"file_path": "board.cir.yaml"}, cir_filename="board.cir.yaml"
    )
    # A write with no path at all.
    assert not agent.is_cir_write("Write", {}, cir_filename="board.cir.yaml")


def test_is_cir_write_catches_a_bash_command_touching_the_cir() -> None:
    # The Bash bypass: sed -i / redirection past the file-write tool gate.
    assert agent.is_cir_write(
        "Bash",
        {"command": "sed -i 's/x/y/' board.cir.yaml"},
        cir_filename="board.cir.yaml",
    )
    assert agent.is_cir_write(
        "Bash",
        {"command": "echo 'name: x' > board.cir.yaml"},
        cir_filename="board.cir.yaml",
    )
    # A Bash command that never names the CIR file is not gated.
    assert not agent.is_cir_write(
        "Bash", {"command": "ls -la"}, cir_filename="board.cir.yaml"
    )


# --------------------------------------------------------------------------
# _normalize — SDK message -> GUI event dicts
# --------------------------------------------------------------------------
def test_normalize_assistant_text_and_tool_use() -> None:
    msg = AssistantMessage(
        content=[
            TextBlock(text="Validating the board."),
            ThinkingBlock(thinking="need to run validate", signature="sig"),
            ToolUseBlock(id="tu-1", name="validate", input={"path": "board.cir.yaml"}),
        ],
        model="claude-opus-4-7",
    )
    events = agent._normalize(msg)
    assert events == [
        {"type": "text", "text": "Validating the board."},
        {"type": "thinking", "text": "need to run validate"},
        {
            "type": "tool_use",
            "id": "tu-1",
            "name": "validate",
            "input": {"path": "board.cir.yaml"},
        },
    ]


def test_normalize_tool_result_on_user_message() -> None:
    msg = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="tu-1",
                content="validation ok",
                is_error=False,
            )
        ]
    )
    events = agent._normalize(msg)
    assert events == [
        {
            "type": "tool_result",
            "tool_use_id": "tu-1",
            "content": "validation ok",
            "is_error": False,
        }
    ]


def test_normalize_plain_user_text_yields_nothing() -> None:
    # A user turn that is just a string carries no events back to the GUI.
    assert agent._normalize(UserMessage(content="hello")) == []


def test_normalize_result_message_is_a_done_event() -> None:
    msg = ResultMessage(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=900,
        is_error=False,
        num_turns=3,
        session_id="sess-1",
        result="all done",
        total_cost_usd=0.0123,
    )
    events = agent._normalize(msg)
    assert events == [
        {
            "type": "done",
            "is_error": False,
            "result": "all done",
            "cost_usd": 0.0123,
        }
    ]


# --------------------------------------------------------------------------
# AgentSession — exercised against a stub ClaudeSDKClient
# --------------------------------------------------------------------------
class _StubClient:
    """A drop-in for ``ClaudeSDKClient`` that records calls and replays msgs."""

    def __init__(self, *, options: Any) -> None:
        self.options = options
        self.connected = False
        self.disconnected = False
        self.queries: list[str] = []
        self._reply: list[Any] = []

    def set_reply(self, messages: list[Any]) -> None:
        self._reply = messages

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        for message in self._reply:
            yield message


@pytest.fixture
def stub_client(monkeypatch: pytest.MonkeyPatch) -> type[_StubClient]:
    """Replace the SDK client class so no live agent is ever launched."""
    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _StubClient)
    return _StubClient


def test_agent_session_unavailable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "agent_available", lambda: False)
    with pytest.raises(agent.AgentUnavailableError):
        agent.AgentSession(Path("/work"))


def test_agent_session_builds_options_from_working_dir(
    stub_client: type[_StubClient],
) -> None:
    session = agent.AgentSession(Path("/work/board"), model="claude-opus-4-7")
    opts = session._options
    assert opts.cwd == "/work/board"
    assert opts.model == "claude-opus-4-7"
    assert opts.permission_mode == "default"
    assert isinstance(opts.mcp_servers, dict)
    assert "ki-mcp-pcb" in opts.mcp_servers
    assert isinstance(opts.system_prompt, str)
    assert "/work/board" in opts.system_prompt


def test_agent_session_connect_and_close(stub_client: type[_StubClient]) -> None:
    session = agent.AgentSession(Path("/work"))
    client: _StubClient = session._client  # type: ignore[assignment]

    async def scenario() -> None:
        await session.connect()
        assert client.connected is True
        await session.aclose()
        assert client.disconnected is True

    asyncio.run(scenario())


def test_agent_session_aclose_is_a_noop_when_not_connected(
    stub_client: type[_StubClient],
) -> None:
    session = agent.AgentSession(Path("/work"))
    client: _StubClient = session._client  # type: ignore[assignment]
    asyncio.run(session.aclose())
    assert client.disconnected is False


def test_agent_session_send_streams_normalized_events(
    stub_client: type[_StubClient],
) -> None:
    session = agent.AgentSession(Path("/work"))
    client: _StubClient = session._client  # type: ignore[assignment]
    client.set_reply(
        [
            AssistantMessage(
                content=[
                    TextBlock(text="Running validate."),
                    ToolUseBlock(id="tu-1", name="validate", input={}),
                ],
                model="claude-opus-4-7",
            ),
            UserMessage(
                content=[
                    ToolResultBlock(tool_use_id="tu-1", content="ok", is_error=False)
                ]
            ),
            ResultMessage(
                subtype="success",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="s1",
                result="done",
                total_cost_usd=0.01,
            ),
        ]
    )

    async def scenario() -> list[dict[str, Any]]:
        await session.connect()
        return [event async for event in session.send("validate my board")]

    events = asyncio.run(scenario())

    assert client.queries == ["validate my board"]
    assert [e["type"] for e in events] == ["text", "tool_use", "tool_result", "done"]
    assert events[0]["text"] == "Running validate."
    assert events[-1]["result"] == "done"
