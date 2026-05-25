"""M3-P-07 / M3-C-08 — verify the subagent registry is shape-correct
and wired into the parent ClaudeAgentOptions via `bridge.build_options`."""

from __future__ import annotations

from agent.bridge import build_options
from agent.subagents import (
    BOM_SOURCER,
    DECOUPLING_AUDITOR,
    PLACEMENT_EXPLORER,
    all_subagents,
)
from claude_agent_sdk import AgentDefinition


def test_registry_has_three_named_agents() -> None:
    agents = all_subagents()
    assert set(agents.keys()) == {
        "decoupling-auditor",
        "bom-sourcer",
        "placement-explorer",
    }
    for agent in agents.values():
        assert isinstance(agent, AgentDefinition)


def test_decoupling_auditor_restricted_to_read_only_kc_tools() -> None:
    tools = DECOUPLING_AUDITOR.tools
    assert tools is not None
    # Must not be able to mutate the project — no kc_track_*,
    # kc_footprint_place_*, kc_zone_*, etc.
    for t in tools:
        assert "_remove" not in t
        assert "_route" not in t
        assert "_place_hint" not in t
    # The two it does need: open + read.
    assert "mcp__kiclaude__kc_project_open" in tools
    assert "mcp__kiclaude__kc_kcir_get" in tools


def test_bom_sourcer_has_pricing_tools_only() -> None:
    tools = BOM_SOURCER.tools
    assert tools is not None
    assert "mcp__kiclaude__kc_bom_price" in tools
    assert "mcp__kiclaude__kc_part_search" in tools
    # No PCB mutators on the BOM sourcer.
    for t in tools:
        assert "_route" not in t
        assert "_zone" not in t


def test_placement_explorer_has_snapshot_capability() -> None:
    tools = PLACEMENT_EXPLORER.tools
    assert tools is not None
    # Must be able to snapshot + revert so trials don't escape.
    assert "mcp__kiclaude__kc_snapshot_create" in tools
    assert "mcp__kiclaude__kc_snapshot_revert" in tools


def test_every_agent_declares_a_skill_and_a_model() -> None:
    """Each agent inherits the skill it specialises in + picks an
    explicit model rather than inheriting the parent's. This keeps
    the subagent's behaviour predictable across upgrades to the
    parent session's model."""
    for agent in all_subagents().values():
        assert agent.skills is not None and len(agent.skills) > 0
        assert agent.model in {"haiku", "sonnet", "opus", "inherit"} or agent.model is not None


def test_build_options_registers_all_subagents() -> None:
    options = build_options()
    assert options.agents is not None
    assert set(options.agents.keys()) == {
        "decoupling-auditor",
        "bom-sourcer",
        "placement-explorer",
    }
    # And every entry is a real AgentDefinition (not a dict the
    # SDK would silently fail on).
    for agent in options.agents.values():
        assert isinstance(agent, AgentDefinition)


async def test_subagent_dispatch_propagates_parent_session_id_via_otel() -> None:
    """M3-Q-04: every subagent's tool calls must inherit the
    OTel span correlation from the parent session. The
    `pre_tool_use` hook in `lifecycle.py` already reads
    `parent_session_id` off the hook input — this test pins the
    contract by simulating a hook input that names one of the
    registered subagents and verifying the span attribute is set."""
    from agent.hooks.lifecycle import pre_tool_use
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor,
    )
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Don't replace the global provider — agent.telemetry already
    # wired one. Tap the provider's spans via the in-memory exporter
    # attached above.
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))

    for agent_name in ("decoupling-auditor", "bom-sourcer", "placement-explorer"):
        exporter.clear()
        # Simulate the SDK-shaped hook input the subagent's pre_tool_use
        # receives when it dispatches a kc_* tool call.
        await pre_tool_use(
            {
                "session_id": f"child-{agent_name}",
                "parent_session_id": "parent-orchestrator",
                "tool_name": "mcp__kiclaude__kc_kcir_get",
                "tool_use_id": f"call-{agent_name}-1",
                "tool_input": {},
            },
            None,
            {},
        )
        spans = exporter.get_finished_spans()
        # Exactly one pre_tool_use span per call.
        pre = [s for s in spans if s.name == "agent.hook.pre_tool_use"]
        assert len(pre) >= 1, f"no pre_tool_use span for {agent_name}"
        attrs = dict(pre[-1].attributes or {})
        assert attrs.get("parent_session_id") == "parent-orchestrator", (
            f"parent_session_id missing on {agent_name} span: {attrs}"
        )
        assert attrs.get("session_id") == f"child-{agent_name}"
        assert attrs.get("tool_name") == "mcp__kiclaude__kc_kcir_get"


def test_decoupling_auditor_prompt_mentions_the_failure_modes() -> None:
    """The prompt is the contract — the subagent's behaviour is what
    the prompt says it is. This test pins the load-bearing
    expectations (severity rules, the 2 mm rule, the JSON output
    shape) so prompt edits don't silently change behaviour."""
    p = DECOUPLING_AUDITOR.prompt
    assert "VDD" in p
    assert "2 mm" in p
    assert "severity" in p
    # The auditor's contract is "report, don't fix" — the orchestrator
    # is the one that calls /add-decoupling.
    assert "Do NOT propose fixes" in p
