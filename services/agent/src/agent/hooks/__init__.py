"""kiclaude agent lifecycle + permission hooks.

The `lifecycle` module hosts the JSONL-emitting PreToolUse/PostToolUse/
SessionStart/SessionEnd hooks (M0-Q-05). The `permission` module hosts
the M1-P-06 PreToolUse permission gate that auto-approves read-only
tools and blocks mutating tools on a UI back-channel.
"""

from __future__ import annotations

from .lifecycle import (
    SINK,
    HookSink,
    post_tool_use,
    pre_tool_use,
    reset_inflight_for_tests,
    session_end,
    session_start,
)
from .permission import (
    PermissionDecision,
    PermissionSettings,
    SnapshotRecorder,
    classify_tool,
    is_mutating,
    permission_hook,
    set_approval_provider,
    set_snapshot_recorder,
)

__all__ = [
    "SINK",
    "HookSink",
    "PermissionDecision",
    "PermissionSettings",
    "SnapshotRecorder",
    "classify_tool",
    "is_mutating",
    "permission_hook",
    "post_tool_use",
    "pre_tool_use",
    "reset_inflight_for_tests",
    "session_end",
    "session_start",
    "set_approval_provider",
    "set_snapshot_recorder",
]
