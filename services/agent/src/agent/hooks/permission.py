"""M1-P-06: PreToolUse permission gate.

Decides whether a kc_* / mcp__kiclaude__kc_* tool call should
proceed, prompt the UI for approval, or auto-deny.

Rules (FR-053, plan §4.2 M1-P-06):

- **Read-only tools** — `kc_kcir_get`, `kc_validate`, `kc_erc`,
  anything matching `kc_*_get` — auto-approve.
- **Mutating tools** — `kc_symbol_*`, `kc_wire_*`, `kc_label_*`,
  `kc_snapshot_*`, `kc_project_save` — block on the UI approval
  back-channel.
- **`trusted_mode: true` in `.claude/settings.json`** — auto-approve
  every kc_* call.
- **Unknown / non-kc_* tools** — defer to the SDK default ("ask"),
  the permission gate doesn't override built-in tools.

The approval back-channel is pluggable: tests inject a callable that
returns the boolean decision; production wires it to the gateway WS
back-channel (M1-T-07 ChatSidebar surfaces the prompt).
"""

from __future__ import annotations

import json
import os
import uuid as _uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


class PermissionDecision(StrEnum):
    """Three-valued outcome of the gate."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalProvider(Protocol):
    """Pluggable approval transport. The default returns DENY (no UI
    is reachable, so we refuse the mutation rather than letting it
    silently succeed); production wires this to the WS gateway."""

    async def __call__(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        session_id: str,
        project_id: str | None,
    ) -> PermissionDecision: ...


@dataclass(frozen=True, slots=True)
class PermissionSettings:
    """Resolved permission configuration. Built from
    `.claude/settings.json` + env overrides at session-start time."""

    trusted_mode: bool = False
    extra_auto_approve: tuple[str, ...] = ()
    extra_auto_deny: tuple[str, ...] = ()

    @classmethod
    def from_settings_json(cls, path: Path | None = None) -> PermissionSettings:
        """Load from a `.claude/settings.json` file. Falls back to the
        all-defaults instance if the file is missing or malformed.

        Env overrides:
        - `KICLAUDE_TRUSTED_MODE=1` forces `trusted_mode=true`.
        """
        trusted = os.environ.get("KICLAUDE_TRUSTED_MODE", "").lower() in {
            "1",
            "true",
            "yes",
        }
        if path is None:
            return cls(trusted_mode=trusted)
        try:
            raw = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return cls(trusted_mode=trusted)
        kiclaude_block = (raw.get("kiclaude") or {}) if isinstance(raw, dict) else {}
        perm = kiclaude_block.get("permissions") or {}
        return cls(
            trusted_mode=trusted or bool(perm.get("trusted_mode", False)),
            extra_auto_approve=tuple(perm.get("auto_approve", [])),
            extra_auto_deny=tuple(perm.get("auto_deny", [])),
        )


# Read-only `kc_*` tool names auto-approved without going to the UI.
_DEFAULT_READ_ONLY = frozenset(
    {
        "kc_ping",
        "kc_kcir_get",
        "kc_validate",
        "kc_erc",
        "kc_mpn_resolve",
        "kc_project_open",
    }
)

# Mutating `kc_*` tool names that need explicit UI approval.
_DEFAULT_MUTATING_PREFIXES = (
    "kc_symbol_",
    "kc_wire_",
    "kc_label_",
    "kc_snapshot_",
)
_DEFAULT_MUTATING_NAMES = frozenset({"kc_project_save"})


async def _default_approval_provider(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    session_id: str,
    project_id: str | None,
) -> PermissionDecision:
    """Production default — refuse anything we can't take to a UI.

    The real provider is set by `set_approval_provider` once the
    gateway-WS approval bridge is up. Until then, mutating tools are
    denied so the agent doesn't silently mutate state.
    """
    _ = (tool_name, tool_input, session_id, project_id)
    return PermissionDecision.DENY


_APPROVAL_PROVIDER: ApprovalProvider = _default_approval_provider


def set_approval_provider(provider: ApprovalProvider | None) -> None:
    """Swap the active approval provider. Pass `None` to restore the
    default (DENY-everything-mutating) fallback."""
    global _APPROVAL_PROVIDER
    _APPROVAL_PROVIDER = provider or _default_approval_provider


class SnapshotRecorder(Protocol):
    """Pluggable auto-snapshot transport. The production wiring posts
    to `POST /project/{id}/snapshot/create` on kiserver before a
    mutating tool runs so the ActivityJournal has a 'before' state to
    revert to (FR-056). Tests inject an in-memory recorder."""

    async def __call__(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        label: str,
        tool_name: str,
        session_id: str,
    ) -> bool: ...


async def _default_snapshot_recorder(
    *,
    project_id: str,
    snapshot_id: str,
    label: str,
    tool_name: str,
    session_id: str,
) -> bool:
    """Production default — no-op. The agent service wires a real
    recorder once kiserver is reachable."""
    _ = (project_id, snapshot_id, label, tool_name, session_id)
    return False


_SNAPSHOT_RECORDER: SnapshotRecorder = _default_snapshot_recorder


def set_snapshot_recorder(recorder: SnapshotRecorder | None) -> None:
    """Swap the auto-snapshot recorder. Pass `None` to restore the
    default no-op."""
    global _SNAPSHOT_RECORDER
    _SNAPSHOT_RECORDER = recorder or _default_snapshot_recorder


def is_mutating(tool_name: str) -> bool:
    """`True` for tools that change KCIR state. Mirrors the journal's
    `MUTATING_TOOLS` list on the frontend so the snapshot-before-run
    contract is identical on both sides of the wire."""
    if not isinstance(tool_name, str) or not tool_name:
        return False
    base = tool_name.split("__")[-1]
    if base in _DEFAULT_MUTATING_NAMES:
        return True
    return any(base.startswith(p) for p in _DEFAULT_MUTATING_PREFIXES)


def classify_tool(name: str, settings: PermissionSettings) -> PermissionDecision:
    """Pure-function classification for a tool name. Independent of
    the approval transport so tests can pin the rules without
    spinning up an async runtime."""
    if not isinstance(name, str) or not name:
        return PermissionDecision.ASK
    # MCP-prefixed names look like `mcp__kiclaude__kc_symbol_add`.
    base = name.split("__")[-1]
    if base in settings.extra_auto_deny:
        return PermissionDecision.DENY
    if base in settings.extra_auto_approve:
        return PermissionDecision.ALLOW
    if settings.trusted_mode:
        return PermissionDecision.ALLOW
    if base in _DEFAULT_READ_ONLY or base.endswith("_get"):
        return PermissionDecision.ALLOW
    if base in _DEFAULT_MUTATING_NAMES or any(
        base.startswith(p) for p in _DEFAULT_MUTATING_PREFIXES
    ):
        return PermissionDecision.ASK
    if not base.startswith("kc_"):
        # Don't override built-in / non-kiclaude tools.
        return PermissionDecision.ASK
    return PermissionDecision.ASK


async def permission_hook(
    input_data: dict[str, Any],
    _tool_use_id: str | None,
    _context: Any,
    *,
    settings_loader: Callable[[], PermissionSettings] | None = None,
    approval_provider: ApprovalProvider | None = None,
    snapshot_recorder: SnapshotRecorder | None = None,
) -> dict[str, Any]:
    """`PreToolUse` permission hook returning the SDK's
    `permissionDecision` envelope.

    The envelope shape matches the Claude Agent SDK's hookspec:
    `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
    "permissionDecision": "allow" | "deny" | "ask"}}`.

    When the decision is ALLOW for a mutating tool, the hook also
    records a "before" snapshot via the active
    [`SnapshotRecorder`][SnapshotRecorder] so the M1-T-08
    ActivityJournal has a per-call revert target (FR-056). The
    snapshot id is propagated in the `permissionDecisionReason` field
    of the SDK envelope so downstream consumers (the gateway tool-use
    broadcaster) can attach it to the `tool_use_start` WS frame.
    """
    settings = (settings_loader or _load_settings)()
    tool_name = input_data.get("tool_name", "")
    project_id = _extract_project_id(input_data)
    session_id = input_data.get("session_id", "") or ""
    decision = classify_tool(tool_name, settings)
    if decision is PermissionDecision.ASK:
        provider = approval_provider or _APPROVAL_PROVIDER
        decision = await provider(
            tool_name=tool_name,
            tool_input=input_data.get("tool_input", {}) or {},
            session_id=session_id,
            project_id=project_id,
        )
    output: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision.value,
    }
    if decision is PermissionDecision.ALLOW and is_mutating(tool_name) and project_id:
        recorder = snapshot_recorder or _SNAPSHOT_RECORDER
        snapshot_id = str(_uuid.uuid4())
        label = f"auto:{tool_name.split('__')[-1]}"
        try:
            recorded = await recorder(
                project_id=project_id,
                snapshot_id=snapshot_id,
                label=label,
                tool_name=tool_name,
                session_id=session_id,
            )
        except Exception:
            recorded = False
        if recorded:
            output["permissionDecisionReason"] = json.dumps(
                {"snapshot_id": snapshot_id, "label": label}, sort_keys=True
            )
    return {"hookSpecificOutput": output}


def _extract_project_id(input_data: dict[str, Any]) -> str | None:
    inp = input_data.get("tool_input")
    if isinstance(inp, dict):
        pid = inp.get("project_id")
        if isinstance(pid, str) and pid:
            return pid
    env = os.environ.get("KICLAUDE_PROJECT_ID")
    return env if env else None


def _load_settings() -> PermissionSettings:
    """Load `.claude/settings.json` from the kiclaude repo root.

    Looks up the kiclaude root via `KICLAUDE_PROJECT_ROOT` env or
    falls back to the agent service's parent directory chain. Returns
    a defaults-only PermissionSettings if nothing matches.
    """
    explicit = os.environ.get("KICLAUDE_PROJECT_ROOT")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit) / ".claude" / "settings.json")
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / ".claude" / "settings.json")
    for path in candidates:
        if path.is_file():
            return PermissionSettings.from_settings_json(path)
    return PermissionSettings.from_settings_json()


__all__ = [
    "ApprovalProvider",
    "PermissionDecision",
    "PermissionSettings",
    "SnapshotRecorder",
    "classify_tool",
    "is_mutating",
    "permission_hook",
    "set_approval_provider",
    "set_snapshot_recorder",
]
