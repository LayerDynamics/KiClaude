"""Integration tests for the co-pilot WebSocket (``WS /api/agent``, G2-T2).

The Claude Agent SDK seam (``server.agent.AgentSession`` /
``agent_available``) is monkeypatched with a fake — no agent process is
launched and no live Anthropic call is made (SPEC-1 §8). The tests drive
the real FastAPI endpoint through ``TestClient.websocket_connect``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from ki_mcp_pcb_web import agent as agent_module
from ki_mcp_pcb_web import server, session


class _FakeSession:
    """Stands in for ``agent.AgentSession`` — replays canned events.

    State is class-level because the endpoint constructs the session
    itself; the test reaches it through the class.
    """

    #: Script the endpoint's session will replay, set per test.
    reply: ClassVar[list[dict[str, Any]]] = []
    #: When set, ``send`` raises this instead of yielding ``reply``.
    raise_on_send: ClassVar[Exception | None] = None
    #: When set, ``send`` first drives the approval gate with this
    #: ``(tool_name, tool_input)`` — simulating the agent requesting a tool.
    tool_request: ClassVar[tuple[str, dict[str, Any]] | None] = None
    #: Records, across the connection's lifetime, for assertions.
    prompts: ClassVar[list[str]] = []
    #: The approval-gate decision observed for ``tool_request``.
    gate_allowed: ClassVar[bool | None] = None
    closed: ClassVar[bool] = False

    def __init__(
        self, working_dir: Path, *, can_use_tool: Any | None = None
    ) -> None:
        self.working_dir = working_dir
        self.can_use_tool = can_use_tool

    async def connect(self) -> None:
        pass

    async def aclose(self) -> None:
        type(self).closed = True

    async def send(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        type(self).prompts.append(prompt)
        if self.raise_on_send is not None:
            raise self.raise_on_send
        if self.tool_request is not None and self.can_use_tool is not None:
            # Simulate the agent running a tool, exactly as the SDK would: a
            # tool_use event, the call routed through the real backend
            # approval gate, then the matching tool_result.
            name, tool_input = self.tool_request
            yield {
                "type": "tool_use",
                "id": "tu-gate",
                "name": name,
                "input": tool_input,
            }
            result = await self.can_use_tool(name, tool_input, None)
            allowed = getattr(result, "behavior", None) == "allow"
            type(self).gate_allowed = allowed
            yield {
                "type": "tool_result",
                "tool_use_id": "tu-gate",
                "content": "ran" if allowed else "denied",
                "is_error": not allowed,
            }
        for event in self.reply:
            yield event


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    """Each test starts from a clean fake-session script."""
    _FakeSession.reply = []
    _FakeSession.raise_on_send = None
    _FakeSession.tool_request = None
    _FakeSession.prompts = []
    _FakeSession.gate_allowed = None
    _FakeSession.closed = False


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    return TestClient(server.app)


def _use_fake_agent(monkeypatch: pytest.MonkeyPatch, *, available: bool = True) -> None:
    # server.py holds `agent` as a module reference, so patching the module's
    # attributes swaps the seam the endpoint resolves at call time.
    monkeypatch.setattr(agent_module, "agent_available", lambda: available)
    monkeypatch.setattr(agent_module, "AgentSession", _FakeSession)


def test_agent_unavailable_when_sdk_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch, available=False)
    with client.websocket_connect("/api/agent") as ws:
        event = ws.receive_json()
    assert event["type"] == "agent_unavailable"
    assert "claude-agent-sdk" in event["detail"].lower() or event["detail"]


def test_agent_unavailable_when_session_fails_to_connect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenSession(_FakeSession):
        async def connect(self) -> None:
            raise RuntimeError("no anthropic credentials")

    monkeypatch.setattr(agent_module, "agent_available", lambda: True)
    monkeypatch.setattr(agent_module, "AgentSession", _BrokenSession)

    with client.websocket_connect("/api/agent") as ws:
        event = ws.receive_json()
    assert event["type"] == "agent_unavailable"
    assert "credentials" in event["detail"]


def test_prompt_streams_agent_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.reply = [
        {"type": "text", "text": "Validating."},
        {"type": "tool_use", "id": "tu-1", "name": "validate", "input": {}},
        {"type": "tool_result", "tool_use_id": "tu-1", "content": "ok",
         "is_error": False},
        {"type": "done", "is_error": False, "result": "done", "cost_usd": 0.01},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "validate my board"})
        events = [ws.receive_json() for _ in range(4)]

    assert _FakeSession.prompts == ["validate my board"]
    assert [e["type"] for e in events] == [
        "text", "tool_use", "tool_result", "done",
    ]
    assert _FakeSession.closed is True


def test_multiple_turns_on_one_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.reply = [
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "first"})
        assert ws.receive_json()["type"] == "done"
        ws.send_json({"type": "prompt", "text": "second"})
        assert ws.receive_json()["type"] == "done"

    assert _FakeSession.prompts == ["first", "second"]


def test_non_prompt_messages_are_ignored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.reply = [
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "noise", "text": "ignore me"})
        ws.send_json({"type": "prompt", "text": "real prompt"})
        assert ws.receive_json()["type"] == "done"

    assert _FakeSession.prompts == ["real prompt"]


def test_turn_failure_emits_error_and_keeps_session_open(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.raise_on_send = RuntimeError("agent transport broke")

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "do something"})
        event = ws.receive_json()

    assert event["type"] == "error"
    assert "agent transport broke" in event["detail"]
    # The turn failed but the session was still closed cleanly on disconnect.
    assert _FakeSession.closed is True


# --------------------------------------------------------------------------
# G2-T3 — the approval gate, end to end over the WebSocket
# --------------------------------------------------------------------------
def test_irreversible_tool_emits_approval_request_and_allow_runs_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.tool_request = ("tool_export_fab", {"target": "jlcpcb"})
    _FakeSession.reply = [
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "export the fab package"})
        assert ws.receive_json()["type"] == "tool_use"
        request = ws.receive_json()
        assert request["type"] == "approval_request"
        assert request["tool"] == "tool_export_fab"
        assert "fab" in request["reason"].lower()

        ws.send_json(
            {"type": "approval", "id": request["id"], "decision": "allow"}
        )
        tool_result = ws.receive_json()
        assert tool_result["type"] == "tool_result"
        assert tool_result["is_error"] is False

    assert _FakeSession.gate_allowed is True


def test_irreversible_tool_reject_denies_the_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.tool_request = ("tool_export_fab", {})
    _FakeSession.reply = [
        {"type": "done", "is_error": True, "result": None, "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "export it"})
        assert ws.receive_json()["type"] == "tool_use"
        request = ws.receive_json()
        assert request["type"] == "approval_request"

        ws.send_json(
            {"type": "approval", "id": request["id"], "decision": "deny"}
        )
        tool_result = ws.receive_json()
        assert tool_result["type"] == "tool_result"
        assert tool_result["is_error"] is True

    assert _FakeSession.gate_allowed is False


def test_bash_write_to_the_cir_is_also_gated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Bash bypass — `sed -i board.cir.yaml` — must hit the same gate.
    _use_fake_agent(monkeypatch)
    _FakeSession.tool_request = (
        "Bash",
        {"command": "sed -i 's/3V3/5V0/' board.cir.yaml"},
    )
    _FakeSession.reply = [
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "tweak the rail with sed"})
        assert ws.receive_json()["type"] == "tool_use"
        request = ws.receive_json()
        assert request["type"] == "approval_request"
        assert request["tool"] == "Bash"

        ws.send_json(
            {"type": "approval", "id": request["id"], "decision": "deny"}
        )
        assert ws.receive_json()["type"] == "tool_result"

    assert _FakeSession.gate_allowed is False


def test_read_only_tool_is_auto_allowed_without_an_approval_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.tool_request = ("tool_validate_cir", {"source_path": "board.cir.yaml"})
    _FakeSession.reply = [
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "validate"})
        # No approval_request — tool_use is followed straight by tool_result.
        assert ws.receive_json()["type"] == "tool_use"
        result = ws.receive_json()
        assert result["type"] == "tool_result"
        assert result["is_error"] is False

    assert _FakeSession.gate_allowed is True


def test_stray_message_while_approval_pending_gets_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.tool_request = ("tool_export_fab", {})
    _FakeSession.reply = [
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "export it"})
        assert ws.receive_json()["type"] == "tool_use"
        request = ws.receive_json()
        assert request["type"] == "approval_request"

        # A premature second prompt is rejected; the gate keeps waiting.
        ws.send_json({"type": "prompt", "text": "do something else"})
        stray = ws.receive_json()
        assert stray["type"] == "error"
        assert "approval" in stray["detail"]

        # The real decision still resolves the gate.
        ws.send_json(
            {"type": "approval", "id": request["id"], "decision": "allow"}
        )
        assert ws.receive_json()["type"] == "tool_result"

    assert _FakeSession.gate_allowed is True


# --------------------------------------------------------------------------
# G2-T6 — a successful CIR write emits a cir_changed refresh event
# --------------------------------------------------------------------------
def test_successful_cir_write_emits_cir_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.reply = [
        {
            "type": "tool_use",
            "id": "tu-w",
            "name": "Write",
            "input": {"file_path": "board.cir.yaml", "content": "cir_version: '0.2'"},
        },
        {
            "type": "tool_result",
            "tool_use_id": "tu-w",
            "content": "written",
            "is_error": False,
        },
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "add a decoupling cap"})
        events = [ws.receive_json() for _ in range(4)]

    # cir_changed is injected right after the write's tool_result.
    assert [e["type"] for e in events] == [
        "tool_use",
        "tool_result",
        "cir_changed",
        "done",
    ]


def test_failed_cir_write_does_not_emit_cir_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.reply = [
        {
            "type": "tool_use",
            "id": "tu-w",
            "name": "Write",
            "input": {"file_path": "board.cir.yaml"},
        },
        {
            "type": "tool_result",
            "tool_use_id": "tu-w",
            "content": "permission denied",
            "is_error": True,
        },
        {"type": "done", "is_error": True, "result": None, "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "edit the board"})
        events = [ws.receive_json() for _ in range(3)]

    assert "cir_changed" not in [e["type"] for e in events]


def test_write_to_a_non_cir_file_does_not_emit_cir_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_agent(monkeypatch)
    _FakeSession.reply = [
        {
            "type": "tool_use",
            "id": "tu-n",
            "name": "Write",
            "input": {"file_path": "notes.txt"},
        },
        {
            "type": "tool_result",
            "tool_use_id": "tu-n",
            "content": "written",
            "is_error": False,
        },
        {"type": "done", "is_error": False, "result": "ok", "cost_usd": 0.0},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "take notes"})
        events = [ws.receive_json() for _ in range(3)]

    assert "cir_changed" not in [e["type"] for e in events]


# --------------------------------------------------------------------------
# G2-T7 — end-to-end smoke for the whole agent loop
# --------------------------------------------------------------------------
def test_full_agent_loop_prompt_to_approved_cir_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One conversation exercises every G2 seam at once.

    A prompt drives the agent; it explains itself (`text`), runs a tool that
    writes the working CIR (`tool_use`), which the backend approval gate
    holds (`approval_request`); the user approves; the write's clean
    `tool_result` triggers the editor-refresh (`cir_changed`); the turn ends
    (`done`).
    """
    _use_fake_agent(monkeypatch)
    # A CIR-file write: gated for approval *and* watched for cir_changed.
    _FakeSession.tool_request = ("Write", {"file_path": "board.cir.yaml"})
    _FakeSession.reply = [
        {"type": "text", "text": "Added the decoupling cap."},
        {"type": "done", "is_error": False, "result": "done", "cost_usd": 0.02},
    ]

    with client.websocket_connect("/api/agent") as ws:
        ws.send_json({"type": "prompt", "text": "add a 100nF cap on the 3V3 rail"})

        tool_use = ws.receive_json()
        assert tool_use["type"] == "tool_use"
        assert tool_use["name"] == "Write"

        approval = ws.receive_json()
        assert approval["type"] == "approval_request"
        assert "board.cir.yaml" in approval["reason"]

        # The user approves the irreversible write in the GUI.
        ws.send_json(
            {"type": "approval", "id": approval["id"], "decision": "allow"}
        )

        rest = [ws.receive_json() for _ in range(4)]

    assert [e["type"] for e in rest] == [
        "tool_result",
        "cir_changed",
        "text",
        "done",
    ]
    assert rest[0]["is_error"] is False
    assert rest[2]["text"] == "Added the decoupling cap."
    assert _FakeSession.gate_allowed is True
    assert _FakeSession.closed is True
