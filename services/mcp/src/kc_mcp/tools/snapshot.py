"""`kc_snapshot_create` — record an undo-able snapshot of the
current KCIR (M1-P-04).

Snapshots are stored in-process keyed by `project_id`. M1-T-08's
ActivityJournal calls `kc_snapshot_revert` (M1-T-08 / FR-056) to
roll the project back to a named snapshot.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post

# In-memory snapshot store: project_id → list of (snapshot_id, label, ts, project_dict).
_SNAPSHOTS: dict[str, list[dict[str, Any]]] = {}
_LOCK = threading.Lock()


@tool(
    "kc_snapshot_create",
    "Snapshot the current KCIR project state under a user-visible "
    "label so kc_snapshot_revert can roll back to it. Returns the "
    "snapshot's uuid + the human-readable timestamp.",
    {
        "project_id": str,
        "label": str,
    },
)
async def kc_snapshot_create(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    label = (args.get("label") or "").strip() or "snapshot"
    if not project_id:
        return error_envelope("`project_id` is required")
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception as e:
        return error_envelope(
            f"kiserver could not return project_id={project_id}: {e}",
            project_id=project_id,
        )
    project = result.get("project")
    if not isinstance(project, dict):
        return error_envelope(f"unexpected /project/{project_id} payload")

    snapshot_id = str(uuid.uuid4())
    ts = datetime.now(UTC).isoformat()
    snapshot = {
        "snapshot_id": snapshot_id,
        "project_id": project_id,
        "label": label,
        "ts": ts,
        "project": project,
    }
    with _LOCK:
        _SNAPSHOTS.setdefault(project_id, []).append(snapshot)

    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "snapshot_id": snapshot_id,
            "label": label,
            "ts": ts,
            "count": len(_SNAPSHOTS[project_id]),
        }
    )


def get_snapshot_project(project_id: str, snapshot_id: str) -> dict[str, Any] | None:
    """Return the stored KCIR project payload for a snapshot id, or
    `None` if the snapshot is unknown. Used by the kiserver direct
    revert route which does an in-process REGISTRY swap and bypasses
    the kiserver HTTP loop that `revert_to_snapshot` takes."""
    with _LOCK:
        for s in _SNAPSHOTS.get(project_id, []):
            if s["snapshot_id"] == snapshot_id:
                return dict(s["project"])
    return None


def get_snapshot_meta(project_id: str, snapshot_id: str) -> dict[str, Any] | None:
    """Return `{label, ts}` for a snapshot id (no KCIR payload)."""
    with _LOCK:
        for s in _SNAPSHOTS.get(project_id, []):
            if s["snapshot_id"] == snapshot_id:
                return {"label": s["label"], "ts": s["ts"]}
    return None


def record_snapshot(project_id: str, snapshot_id: str, label: str, project: dict[str, Any]) -> str:
    """Insert a snapshot into the in-process store from outside the
    MCP @tool entry point (the agent permission hook uses this to
    auto-snapshot before a mutation). Returns the `ts` written."""
    ts = datetime.now(UTC).isoformat()
    with _LOCK:
        _SNAPSHOTS.setdefault(project_id, []).append(
            {
                "snapshot_id": snapshot_id,
                "project_id": project_id,
                "label": label or "auto",
                "ts": ts,
                "project": dict(project),
            }
        )
    return ts


def list_snapshots(project_id: str) -> list[dict[str, Any]]:
    """Internal: every snapshot for a project (without the full KCIR
    payload so logging this is cheap). Used by `kc_snapshot_revert`
    and the M1-T-08 ActivityJournal."""
    with _LOCK:
        out = []
        for s in _SNAPSHOTS.get(project_id, []):
            out.append(
                {
                    "snapshot_id": s["snapshot_id"],
                    "label": s["label"],
                    "ts": s["ts"],
                }
            )
        return out


async def revert_to_snapshot(project_id: str, snapshot_id: str) -> dict[str, Any]:
    """Restore the project to a snapshot. Returns the standard
    `{ok, project_id, snapshot_id}` envelope."""
    with _LOCK:
        history = _SNAPSHOTS.get(project_id, [])
        snapshot = next((s for s in history if s["snapshot_id"] == snapshot_id), None)
    if snapshot is None:
        return error_envelope(
            f"no snapshot {snapshot_id} for project {project_id}",
            project_id=project_id,
        )
    try:
        await kiserver_post(
            f"/project/{project_id}/replace",
            {"project": snapshot["project"]},
        )
    except Exception as e:
        return error_envelope(
            f"kiserver replace failed during revert: {e}",
            project_id=project_id,
        )
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "snapshot_id": snapshot_id,
            "reverted_to_label": snapshot["label"],
            "reverted_to_ts": snapshot["ts"],
        }
    )


def _clear_for_tests() -> None:
    with _LOCK:
        _SNAPSHOTS.clear()


@tool(
    "kc_snapshot_revert",
    "Roll the project state back to a named snapshot id previously "
    "returned by kc_snapshot_create. Used by the ActivityJournal's "
    "per-call revert (FR-056).",
    {
        "project_id": str,
        "snapshot_id": str,
    },
)
async def kc_snapshot_revert(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    snapshot_id = args.get("snapshot_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    if not snapshot_id:
        return error_envelope("`snapshot_id` is required")
    # `revert_to_snapshot` already returns the envelope shape, so
    # return it as-is — wrapping again would nest the content array.
    return await revert_to_snapshot(project_id, snapshot_id)


__all__ = [
    "kc_snapshot_create",
    "kc_snapshot_revert",
    "list_snapshots",
    "revert_to_snapshot",
]
