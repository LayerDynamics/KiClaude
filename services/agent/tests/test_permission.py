"""M1-P-06 acceptance tests for the PreToolUse permission gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.hooks.permission import (
    PermissionDecision,
    PermissionSettings,
    classify_tool,
    is_mutating,
    permission_hook,
    targets_signoff,
)


def test_classify_read_only_tools_are_auto_allowed() -> None:
    s = PermissionSettings()
    for name in ["kc_ping", "kc_kcir_get", "kc_validate", "kc_erc", "kc_mpn_resolve"]:
        assert classify_tool(name, s) is PermissionDecision.ALLOW


def test_classify_get_suffix_auto_allows_future_read_only_tools() -> None:
    """Any future `kc_foo_get` is treated as read-only without an
    explicit registry entry — encourages naming hygiene."""
    s = PermissionSettings()
    assert classify_tool("kc_design_rules_get", s) is PermissionDecision.ALLOW


def test_classify_mutating_tools_require_ask() -> None:
    s = PermissionSettings()
    for name in [
        "kc_symbol_add",
        "kc_symbol_edit",
        "kc_wire_connect",
        "kc_label_attach",
        "kc_snapshot_create",
        "kc_project_save",
    ]:
        assert classify_tool(name, s) is PermissionDecision.ASK


def test_classify_handles_mcp_prefixed_names() -> None:
    s = PermissionSettings()
    assert (
        classify_tool("mcp__kiclaude__kc_symbol_add", s) is PermissionDecision.ASK
    )
    assert (
        classify_tool("mcp__kiclaude__kc_validate", s) is PermissionDecision.ALLOW
    )


def test_trusted_mode_auto_approves_everything_kc() -> None:
    s = PermissionSettings(trusted_mode=True)
    assert classify_tool("kc_symbol_add", s) is PermissionDecision.ALLOW
    assert classify_tool("kc_snapshot_create", s) is PermissionDecision.ALLOW


def test_extra_auto_deny_overrides_default() -> None:
    s = PermissionSettings(extra_auto_deny=("kc_validate",))
    assert classify_tool("kc_validate", s) is PermissionDecision.DENY


def test_extra_auto_approve_overrides_default_ask() -> None:
    s = PermissionSettings(extra_auto_approve=("kc_symbol_add",))
    assert classify_tool("kc_symbol_add", s) is PermissionDecision.ALLOW


def test_non_kc_tool_defers_to_ask() -> None:
    """Built-in / non-kiclaude tools (Read, Bash, etc.) aren't
    overridden — the SDK's default policy applies."""
    s = PermissionSettings()
    assert classify_tool("Bash", s) is PermissionDecision.ASK


def test_settings_loads_trusted_mode_from_json(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"kiclaude": {"permissions": {"trusted_mode": True}}})
    )
    s = PermissionSettings.from_settings_json(settings_path)
    assert s.trusted_mode is True


def test_settings_env_override_forces_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KICLAUDE_TRUSTED_MODE", "1")
    s = PermissionSettings.from_settings_json(None)
    assert s.trusted_mode is True


def test_settings_missing_file_returns_defaults(tmp_path: Path) -> None:
    s = PermissionSettings.from_settings_json(tmp_path / "nope.json")
    assert s.trusted_mode is False
    assert s.extra_auto_approve == ()
    assert s.extra_auto_deny == ()


async def test_permission_hook_allow_envelope() -> None:
    out = await permission_hook(
        {"tool_name": "kc_kcir_get", "tool_input": {}, "session_id": "s1"},
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


async def test_permission_hook_routes_through_approval_provider() -> None:
    captured: dict[str, str] = {}

    async def fake_provider(
        *, tool_name: str, tool_input, session_id, project_id
    ) -> PermissionDecision:  # type: ignore[no-untyped-def]
        captured["tool_name"] = tool_name
        captured["session_id"] = session_id
        return PermissionDecision.ALLOW

    out = await permission_hook(
        {
            "tool_name": "kc_symbol_add",
            "tool_input": {"project_id": "p1"},
            "session_id": "s1",
        },
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
        approval_provider=fake_provider,
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert captured == {"tool_name": "kc_symbol_add", "session_id": "s1"}


async def test_permission_hook_default_provider_denies() -> None:
    """With no approval transport wired, mutating calls must be
    denied — not silently allowed."""
    out = await permission_hook(
        {"tool_name": "kc_symbol_add", "tool_input": {}, "session_id": "s1"},
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ----------------------------------------------------------------
# M1-T-08 auto-snapshot path.
# ----------------------------------------------------------------


def test_is_mutating_covers_mcp_prefixed_names() -> None:
    assert is_mutating("kc_symbol_add") is True
    assert is_mutating("mcp__kiclaude__kc_wire_connect") is True
    assert is_mutating("kc_kcir_get") is False
    assert is_mutating("kc_validate") is False
    assert is_mutating("") is False


async def test_permission_hook_records_snapshot_before_mutation() -> None:
    """When the gate ALLOWs a mutating call, the snapshot recorder
    must be invoked once with the resolved project_id, and the
    snapshot_id must surface in `permissionDecisionReason`."""
    captured: dict[str, object] = {}

    async def recorder(
        *, project_id, snapshot_id, label, tool_name, session_id
    ) -> bool:  # type: ignore[no-untyped-def]
        captured["project_id"] = project_id
        captured["snapshot_id"] = snapshot_id
        captured["label"] = label
        captured["tool_name"] = tool_name
        captured["session_id"] = session_id
        return True

    async def approver(
        *, tool_name, tool_input, session_id, project_id
    ) -> PermissionDecision:  # type: ignore[no-untyped-def]
        return PermissionDecision.ALLOW

    out = await permission_hook(
        {
            "tool_name": "kc_symbol_add",
            "tool_input": {"project_id": "proj-Z"},
            "session_id": "sess-1",
        },
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
        approval_provider=approver,
        snapshot_recorder=recorder,
    )
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "allow"
    assert "permissionDecisionReason" in spec
    reason = json.loads(spec["permissionDecisionReason"])
    assert reason["snapshot_id"] == captured["snapshot_id"]
    assert reason["label"].startswith("auto:")
    assert captured["project_id"] == "proj-Z"
    assert captured["tool_name"] == "kc_symbol_add"


async def test_permission_hook_skips_snapshot_for_read_only_tools() -> None:
    """Read-only tools must not trigger an auto-snapshot — wasteful
    and would crowd the journal."""
    called = {"n": 0}

    async def recorder(
        *, project_id, snapshot_id, label, tool_name, session_id
    ) -> bool:  # type: ignore[no-untyped-def]
        called["n"] += 1
        return True

    out = await permission_hook(
        {
            "tool_name": "kc_kcir_get",
            "tool_input": {"project_id": "p1"},
            "session_id": "s1",
        },
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
        snapshot_recorder=recorder,
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "permissionDecisionReason" not in out["hookSpecificOutput"]
    assert called["n"] == 0


async def test_permission_hook_skips_snapshot_when_denied() -> None:
    """A denied mutation must not record a snapshot — the operation
    never ran."""
    called = {"n": 0}

    async def recorder(
        *, project_id, snapshot_id, label, tool_name, session_id
    ) -> bool:  # type: ignore[no-untyped-def]
        called["n"] += 1
        return True

    async def approver(
        *, tool_name, tool_input, session_id, project_id
    ) -> PermissionDecision:  # type: ignore[no-untyped-def]
        return PermissionDecision.DENY

    out = await permission_hook(
        {
            "tool_name": "kc_symbol_add",
            "tool_input": {"project_id": "p1"},
            "session_id": "s1",
        },
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
        approval_provider=approver,
        snapshot_recorder=recorder,
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert called["n"] == 0


async def test_permission_hook_swallows_recorder_exceptions() -> None:
    """A failing recorder must not block the call — the user's
    mutation is more important than the journal."""

    async def recorder(
        *, project_id, snapshot_id, label, tool_name, session_id
    ) -> bool:  # type: ignore[no-untyped-def]
        raise RuntimeError("kiserver down")

    async def approver(
        *, tool_name, tool_input, session_id, project_id
    ) -> PermissionDecision:  # type: ignore[no-untyped-def]
        return PermissionDecision.ALLOW

    out = await permission_hook(
        {
            "tool_name": "kc_wire_connect",
            "tool_input": {"project_id": "p1"},
            "session_id": "s1",
        },
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
        approval_provider=approver,
        snapshot_recorder=recorder,
    )
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "allow"
    # No reason field — the recorder failed silently.
    assert "permissionDecisionReason" not in spec


# --- M5 signoff hard gate (FR-081-adjacent / SPEC §11 M5) -----------------


def test_targets_signoff_detects_flag_keys_and_nesting() -> None:
    assert targets_signoff({"signoff": {"ddr_reviewed": True}}) is True
    assert targets_signoff({"ddr_reviewed": True}) is True
    assert targets_signoff({"pcb": {"signoff": {"rf_reviewed": True}}}) is True
    assert targets_signoff({"patch": [{"bga_fanout_reviewed": True}]}) is True
    # Normal declarative tool inputs never trip the guard.
    assert targets_signoff({"project_id": "p1", "refdes": "U1"}) is False
    assert targets_signoff({"net": "GND", "layer": "In1.Cu"}) is False
    assert targets_signoff("not a dict") is False


async def test_permission_hook_denies_signoff_even_in_trusted_mode() -> None:
    """The LLM can never flip pcb.signoff — the hard gate overrides
    trusted mode (which otherwise auto-approves every kc_* call)."""
    out = await permission_hook(
        {
            "tool_name": "kc_project_save",
            "tool_input": {"project_id": "p1", "signoff": {"ddr_reviewed": True}},
            "session_id": "s1",
        },
        None,
        None,
        settings_loader=lambda: PermissionSettings(trusted_mode=True),
    )
    hook_out = out["hookSpecificOutput"]
    assert hook_out["permissionDecision"] == "deny"
    assert "sign-off" in hook_out["permissionDecisionReason"]


async def test_permission_hook_denies_nested_signoff_in_any_tool() -> None:
    out = await permission_hook(
        {
            "tool_name": "mcp__kiclaude__kc_kcir_get",
            "tool_input": {
                "project_id": "p1",
                "patch": {"pcb": {"signoff": {"rf_reviewed": True}}},
            },
            "session_id": "s1",
        },
        None,
        None,
        settings_loader=lambda: PermissionSettings(trusted_mode=True),
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_permission_hook_allows_normal_call_without_signoff() -> None:
    """Regression: the signoff guard must not over-trigger on ordinary
    read-only calls."""
    out = await permission_hook(
        {"tool_name": "kc_kcir_get", "tool_input": {"project_id": "p1"}, "session_id": "s1"},
        None,
        None,
        settings_loader=lambda: PermissionSettings(),
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
