"""M3 high-speed / SI Claude-facing tools (SPEC §A.2.1).

The declarative surface Claude uses for controlled-impedance and
mixed-signal work — the counterparts to the KC0xx validators and the
M3-T-03/04 UI panels:

- `kc_decoupling_check`  → KC020 (every IC has a bypass cap)
- `kc_partition_check`   → KC050 (analog/digital ground isolation)
- `kc_impedance_check`   → KC040 (controlled-impedance achievability)
- `kc_diffpair_declare`  → declare a diff pair (writes pcb.diff_pairs)
- `kc_length_match_set`  → set a length-match group's tolerance

The three `*_check` tools are read-only and reuse the
[`_run_validators`][kc_mcp.tools.validate] pass so the MCP surface and
the `kc_validate` report never drift. The two declarative mutators reuse
the tested UI-tool logic (`ui_diffpair_set` / `ui_lengthgroup_set`) and
persist via kiserver's `/project/{id}/replace`.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post
from kc_mcp.tools.validate import (
    _microstrip_z0,
    _net_class_width,
    _outer_microstrip_geometry,
    _run_validators,
)
from kc_mcp.ui_tools.diffpair_edit import ui_diffpair_set
from kc_mcp.ui_tools.lengthgroup_edit import ui_lengthgroup_set


async def _fetch_project(project_id: str) -> dict[str, Any]:
    result = await kiserver_get(f"/project/{project_id}")
    return result.get("project", {}) or {}


def _findings_for(project: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [f for f in _run_validators(project) if f["code"] == code]


@tool(
    "kc_decoupling_check",
    "Report ICs that are missing a bypass capacitor on a power rail "
    "(KC020). Read-only. Returns `{ok, missing[]}` where each entry is "
    "a finding with `severity`, `message`, `target_uuid`.",
    {"project_id": str},
)
async def kc_decoupling_check(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    try:
        project = await _fetch_project(project_id)
    except Exception as e:
        return error_envelope(f"kiserver GET /project/{project_id} failed: {e}")
    missing = _findings_for(project, "KC020")
    return envelope({"ok": True, "project_id": project_id, "missing": missing})


@tool(
    "kc_partition_check",
    "Report analog/digital ground-partition violations — split grounds "
    "tied by more than one bridge (KC050). Read-only. Returns "
    "`{ok, violations[]}`.",
    {"project_id": str},
)
async def kc_partition_check(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    try:
        project = await _fetch_project(project_id)
    except Exception as e:
        return error_envelope(f"kiserver GET /project/{project_id} failed: {e}")
    violations = _findings_for(project, "KC050")
    return envelope({"ok": True, "project_id": project_id, "violations": violations})


@tool(
    "kc_impedance_check",
    "Check controlled-impedance nets against the declared stackup "
    "(KC040). Read-only. With `net`, checks one net; otherwise every "
    "net carrying a `target_impedance_ohm`. Returns `{ok, results[]}` "
    "with the target, the estimated achieved Zo, and a status.",
    {"project_id": str, "net": str},
)
async def kc_impedance_check(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    want = (args.get("net") or "").strip()
    try:
        project = await _fetch_project(project_id)
    except Exception as e:
        return error_envelope(f"kiserver GET /project/{project_id} failed: {e}")

    pcb = project.get("pcb", {}) or {}
    er, h = _outer_microstrip_geometry(project.get("stackup", {}) or {})
    results: list[dict[str, Any]] = []
    for net in pcb.get("nets", []) or []:
        target = net.get("target_impedance_ohm")
        name = net.get("name") or ""
        if not target or (want and name != want):
            continue
        width = _net_class_width(project, net.get("class"))
        if er is None or h is None or not width:
            results.append(
                {
                    "net": name,
                    "target_ohm": float(target),
                    "achieved_ohm": None,
                    "off_pct": None,
                    "status": "unknown",
                    "reason": "no outer-dielectric Er/height or net-class width",
                }
            )
            continue
        z0 = _microstrip_z0(width, h, er)
        off = abs(z0 - float(target)) / float(target)
        status = "ok" if off <= 0.10 else ("warning" if off <= 0.20 else "error")
        results.append(
            {
                "net": name,
                "target_ohm": float(target),
                "achieved_ohm": round(z0, 2),
                "off_pct": round(off * 100.0, 1),
                "status": status,
                "width_mm": width,
            }
        )
    return envelope({"ok": True, "project_id": project_id, "results": results})


@tool(
    "kc_diffpair_declare",
    "Declare a differential pair from two nets — writes "
    "`pcb.diff_pairs` and the mutual `Net.diff_pair` back-refs, then "
    "saves. Mutating (gated by PreToolUse).",
    {
        "project_id": str,
        "net_a": str,
        "net_b": str,
        "target_impedance": float,
        "length_match_group": str,
    },
)
async def kc_diffpair_declare(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    net_a = (args.get("net_a") or "").strip()
    net_b = (args.get("net_b") or "").strip()
    if not project_id or not net_a or not net_b:
        return error_envelope("`project_id`, `net_a`, and `net_b` are required")
    group = (args.get("length_match_group") or "").strip()
    name = group or f"{net_a}__{net_b}"
    try:
        project = await _fetch_project(project_id)
    except Exception as e:
        return error_envelope(f"kiserver GET /project/{project_id} failed: {e}")

    result = ui_diffpair_set(
        project,
        name=name,
        net_positive=net_a,
        net_negative=net_b,
        target_impedance_ohms=args.get("target_impedance"),
        length_group=group or None,
    )
    if not result.get("ok"):
        return error_envelope(result.get("error", "diff-pair declaration failed"))
    try:
        await kiserver_post(f"/project/{project_id}/replace", {"project": project})
    except Exception as e:
        return error_envelope(f"kiserver replace failed: {e}")
    return envelope({"ok": True, "project_id": project_id, "diff_pair": name})


@tool(
    "kc_length_match_set",
    "Set (or create) a length-match group's tolerance — writes "
    "`pcb.length_groups`, then saves. Mutating (gated by PreToolUse).",
    {"project_id": str, "group": str, "tolerance_mm": float},
)
async def kc_length_match_set(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    group = (args.get("group") or "").strip()
    if not project_id or not group:
        return error_envelope("`project_id` and `group` are required")
    try:
        project = await _fetch_project(project_id)
    except Exception as e:
        return error_envelope(f"kiserver GET /project/{project_id} failed: {e}")

    result = ui_lengthgroup_set(project, name=group, tolerance_mm=args.get("tolerance_mm"))
    if not result.get("ok"):
        return error_envelope(result.get("error", "length-match update failed"))
    try:
        await kiserver_post(f"/project/{project_id}/replace", {"project": project})
    except Exception as e:
        return error_envelope(f"kiserver replace failed: {e}")
    return envelope({"ok": True, "project_id": project_id, "group": group})


__all__ = [
    "kc_decoupling_check",
    "kc_diffpair_declare",
    "kc_impedance_check",
    "kc_length_match_set",
    "kc_partition_check",
]
