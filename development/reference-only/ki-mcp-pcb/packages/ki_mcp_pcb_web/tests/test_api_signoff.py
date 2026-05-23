"""Integration tests for the sign-off PATCH endpoint and the
agent-gate regression that proves an LLM still cannot flip the flags
silently (SPEC-1 G4-T5 + CLAUDE.md sign-off rule).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_web import agent, session
from ki_mcp_pcb_web.server import app

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(session.WORKDIR_ENV, str(tmp_path))
    return TestClient(app)


def _seed(client: TestClient) -> None:
    """PUT blinky as the working CIR — fresh signoff (all False, no reviewer)."""
    text = (EXAMPLES / "blinky.yaml").read_text(encoding="utf-8")
    assert client.put("/api/cir", json={"text": text}).status_code == 200


# --------------------------------------------------------------------------
# PATCH /api/cir/signoff — partial updates
# --------------------------------------------------------------------------
def test_patch_signoff_flips_only_the_field_set(client: TestClient) -> None:
    _seed(client)
    response = client.patch("/api/cir/signoff", json={"rf_reviewed": True})

    assert response.status_code == 200
    body = response.json()
    assert body["board"]["signoff"]["rf_reviewed"] is True
    # Untouched fields stayed False / None.
    assert body["board"]["signoff"]["ddr_reviewed"] is False
    assert body["board"]["signoff"]["bga_fanout_reviewed"] is False
    assert body["board"]["signoff"]["reviewer"] is None


def test_patch_signoff_writes_through_to_disk(
    tmp_path: Path, client: TestClient
) -> None:
    _seed(client)
    client.patch(
        "/api/cir/signoff",
        json={"ddr_reviewed": True, "reviewer": "rdo"},
    )

    # Re-parse the on-disk YAML directly — the change is persisted, not
    # just echoed back.
    text = session.cir_path().read_text(encoding="utf-8")
    board = parse_yaml(text)
    assert board.signoff.ddr_reviewed is True
    assert board.signoff.reviewer == "rdo"
    assert board.signoff.rf_reviewed is False


def test_patch_signoff_unset_fields_preserve_previous_value(
    client: TestClient,
) -> None:
    _seed(client)
    # First flip rf_reviewed + reviewer.
    client.patch(
        "/api/cir/signoff",
        json={"rf_reviewed": True, "reviewer": "rdo"},
    )
    # Now flip just bga_fanout_reviewed — rf + reviewer must stay.
    body = client.patch(
        "/api/cir/signoff", json={"bga_fanout_reviewed": True}
    ).json()
    assert body["board"]["signoff"]["rf_reviewed"] is True
    assert body["board"]["signoff"]["reviewer"] == "rdo"
    assert body["board"]["signoff"]["bga_fanout_reviewed"] is True


def test_patch_signoff_400s_when_no_working_cir(client: TestClient) -> None:
    response = client.patch("/api/cir/signoff", json={"rf_reviewed": True})
    assert response.status_code == 400
    assert "no working CIR" in response.json()["detail"]


# --------------------------------------------------------------------------
# Agent-gate regression — an LLM cannot flip sign-off silently
# --------------------------------------------------------------------------
def test_agent_write_that_flips_signoff_is_gated(client: TestClient) -> None:
    """A Write tool call that lands a board.cir.yaml flipping a signoff
    flag must still go through the approval gate — the spec is explicit
    that an LLM may not flip these on its own."""
    # The gate is name+input-based, not content-based: any Write of
    # board.cir.yaml triggers approval, regardless of what's in the
    # content (which is exactly the point — the human reviews the
    # diff in the chat panel).
    reason = agent.approval_reason(
        "Write",
        {
            "file_path": "board.cir.yaml",
            "content": "signoff:\n  rf_reviewed: true\n",
        },
        cir_filename="board.cir.yaml",
    )
    assert reason is not None
    assert "board.cir.yaml" in reason

    # And the Bash-bypass path equally — `sed -i .. board.cir.yaml` is
    # treated as a CIR write so it can't slip past the gate either.
    bash_reason = agent.approval_reason(
        "Bash",
        {"command": "sed -i 's/rf_reviewed: false/rf_reviewed: true/' board.cir.yaml"},
        cir_filename="board.cir.yaml",
    )
    assert bash_reason is not None


def test_signoff_patch_endpoint_is_not_in_agent_tool_surface() -> None:
    """The PATCH endpoint is a human-only surface. The agent's MCP
    toolset (ki_mcp_pcb_server) does not expose anything that touches
    signoff directly — sanity-check that the tool registry has no
    'signoff' tool the agent could call instead of going through Write."""
    from ki_mcp_pcb_server import tools as srv_tools

    tool_names = [
        name for name in dir(srv_tools) if name.startswith("tool_")
    ]
    assert not any("signoff" in name.lower() for name in tool_names), (
        "Sign-off must remain human-only — no MCP tool may flip it directly."
    )
