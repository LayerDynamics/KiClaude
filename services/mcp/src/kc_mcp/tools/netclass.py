"""`kc_netclass_set` + `kc_netclass_list` — per-PCB net-class
management (M2-P-04).

A net class bundles the per-net constraints the M2 walk-around router
and DRC kernel honor (trace width, clearance, via size, diff-pair
geometry). `kc_netclass_set` upserts; `kc_netclass_list` reads back
the current set so the agent can show its work.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post


@tool(
    "kc_netclass_set",
    "Upsert a net class. Use this to widen power nets, declare a "
    "diff-pair class for USB / LVDS, or tighten clearance on a "
    "controlled-impedance bus. Returns the updated class + the list "
    "of nets currently bound to it.",
    {
        "project_id": str,
        "name": str,
        "description": str,
        "clearance_mm": float,
        "trace_width_mm": float,
        "via_drill_mm": float,
        "via_diameter_mm": float,
        "diff_pair_width_mm": float,
        "diff_pair_gap_mm": float,
        "bind_nets": list[str],
    },
)
async def kc_netclass_set(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    name = (args.get("name") or "").strip()
    if not project_id or not name:
        return error_envelope("`project_id` and `name` are required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    pcb = project.setdefault("pcb", {})
    classes: list[dict[str, Any]] = pcb.setdefault("net_classes", [])
    existing = next((c for c in classes if c.get("name") == name), None)
    if existing is None:
        existing = {
            "name": name,
            "description": "",
            "clearance_mm": 0.2,
            "trace_width_mm": 0.25,
            "via_drill_mm": 0.3,
            "via_diameter_mm": 0.6,
            "diff_pair_width_mm": None,
            "diff_pair_gap_mm": None,
        }
        classes.append(existing)

    for key in (
        "description",
        "clearance_mm",
        "trace_width_mm",
        "via_drill_mm",
        "via_diameter_mm",
    ):
        if key in args and args[key] is not None:
            existing[key] = str(args[key]) if key == "description" else float(args[key])
    for key in ("diff_pair_width_mm", "diff_pair_gap_mm"):
        if key in args and args[key] is not None:
            existing[key] = float(args[key])

    bind_nets = args.get("bind_nets") or []
    bound: list[str] = []
    if bind_nets:
        nets = pcb.setdefault("nets", [])
        target_names = {str(n).strip() for n in bind_nets if str(n).strip()}
        for net in nets:
            if net.get("name") in target_names:
                # KCIR `Net.class` is a `NetClassRef(String)`; serde
                # flattens it to a `[name]` tuple on the wire. We
                # accept both shapes so existing fixtures keep working.
                net["class"] = [name]
                bound.append(net["name"])
    try:
        await kiserver_post(f"/project/{project_id}/replace", {"project": project})
    except Exception as e:
        return error_envelope(
            f"kiserver replace failed: {e}",
            project_id=project_id,
        )
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "net_class": existing,
            "bound_nets": bound,
        }
    )


@tool(
    "kc_netclass_list",
    "List all net classes declared on a PCB and which nets are bound to each. Read-only.",
    {"project_id": str},
)
async def kc_netclass_list(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    pcb = project.get("pcb") or {}
    nets = pcb.get("nets") or []
    classes = pcb.get("net_classes") or []
    bindings: dict[str, list[str]] = {c.get("name", ""): [] for c in classes}
    for net in nets:
        cls = net.get("class")
        cls_name = ""
        if isinstance(cls, list) and cls:
            cls_name = str(cls[0])
        elif isinstance(cls, str):
            cls_name = cls
        elif isinstance(cls, dict):
            cls_name = str(cls.get("0") or cls.get("name") or "")
        if cls_name in bindings:
            bindings[cls_name].append(net.get("name", ""))
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "classes": classes,
            "bindings": bindings,
        }
    )


async def _fetch_project(project_id: str) -> dict[str, Any] | None:
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception:
        return None
    project = result.get("project")
    if not isinstance(project, dict):
        return None
    return project


__all__ = ["kc_netclass_list", "kc_netclass_set"]
