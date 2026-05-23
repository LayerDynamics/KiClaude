"""`kc_via_add_hint` — declarative via placement (M2-P-04).

Vias are inserted at the endpoint of a named refdes.pad or at a
waypoint hint along an existing track. Claude never picks a raw xy
— the M2-T-03 routing UI surface exposes a `ui_via_place_xy` for
the drag-drop case.
"""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post


@tool(
    "kc_via_add_hint",
    "Drop a via tied to a net. Position is resolved from `at_pad` "
    '("U1.7") or `at_track_uuid` (centerpoint of the named track). '
    "Returns the new via uuid + resolved coordinate. The `kind` "
    'argument ("", "blind", "buried") gates which layer pair is '
    "allowed: blind/buried require an inner layer pair.",
    {
        "project_id": str,
        "net": str,
        "at_pad": str,
        "at_track_uuid": str,
        "from_layer": str,
        "to_layer": str,
        "drill_mm": float,
        "diameter_mm": float,
        "kind": str,
    },
)
async def kc_via_add_hint(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    net = (args.get("net") or "").strip()
    at_pad = (args.get("at_pad") or "").strip()
    at_track_uuid = (args.get("at_track_uuid") or "").strip()
    if not project_id or not net:
        return error_envelope("`project_id` and `net` are required")
    if not at_pad and not at_track_uuid:
        return error_envelope("either `at_pad` or `at_track_uuid` is required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    pcb = project.setdefault("pcb", {})

    xy: tuple[float, float] | None = None
    reasoning = ""
    if at_pad:
        xy = _resolve_pad(pcb, at_pad)
        reasoning = f"at pad {at_pad}"
    if xy is None and at_track_uuid:
        xy = _resolve_track_midpoint(pcb, at_track_uuid)
        reasoning = f"midpoint of track {at_track_uuid}"
    if xy is None:
        return error_envelope(
            "could not resolve via position from the supplied hints",
            project_id=project_id,
        )

    kind = (args.get("kind") or "").strip()
    from_layer = (args.get("from_layer") or "F.Cu").strip()
    to_layer = (args.get("to_layer") or "B.Cu").strip()
    if kind in {"blind", "buried"}:
        # Blind/buried require at least one of from/to to be an inner
        # copper layer (i.e. not F.Cu/B.Cu only).
        outer = {"F.Cu", "B.Cu"}
        if from_layer in outer and to_layer in outer:
            return error_envelope(
                f"{kind} via must terminate on an inner layer",
                from_layer=from_layer,
                to_layer=to_layer,
            )

    drill_mm = float(args.get("drill_mm") or 0.3)
    diameter_mm = float(args.get("diameter_mm") or 0.6)
    via_uuid = str(uuid.uuid4())
    vias = pcb.setdefault("vias", [])
    vias.append(
        {
            "uuid": via_uuid,
            "net": net,
            "position_mm": [xy[0], xy[1]],
            "from_layer": from_layer,
            "to_layer": to_layer,
            "drill_mm": drill_mm,
            "diameter_mm": diameter_mm,
            "kind": kind,
            "locked": False,
        }
    )
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
            "via_uuid": via_uuid,
            "net": net,
            "resolved_position_mm": [xy[0], xy[1]],
            "reasoning": reasoning,
        }
    )


# ---------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------


def _resolve_pad(pcb: dict[str, Any], waypoint: str) -> tuple[float, float] | None:
    if "." not in waypoint:
        return None
    refdes, pad_num = waypoint.split(".", 1)
    refdes, pad_num = refdes.strip(), pad_num.strip()
    for fp in pcb.get("footprints", []) or []:
        if fp.get("refdes") != refdes:
            continue
        fp_xy = fp.get("position_mm") or [0.0, 0.0]
        fx, fy = float(fp_xy[0]), float(fp_xy[1])
        for pad in fp.get("pads", []) or []:
            if str(pad.get("number")) == pad_num:
                pxy = pad.get("position_mm") or [0.0, 0.0]
                return (fx + float(pxy[0]), fy + float(pxy[1]))
        return (fx, fy)
    return None


def _resolve_track_midpoint(pcb: dict[str, Any], track_uuid: str) -> tuple[float, float] | None:
    for t in pcb.get("tracks", []) or []:
        if t.get("uuid") != track_uuid:
            continue
        pts = t.get("points_mm") or []
        if not pts:
            return None
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    return None


async def _fetch_project(project_id: str) -> dict[str, Any] | None:
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception:
        return None
    project = result.get("project")
    if not isinstance(project, dict):
        return None
    return project


__all__ = ["kc_via_add_hint"]
