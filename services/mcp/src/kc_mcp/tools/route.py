"""`kc_track_route` + `kc_track_remove` — declarative track routing on
the PCB (M2-P-04).

`kc_track_route` takes a net name + a list of waypoint hints
(refdes.pad pairs, e.g. `["U1.7", "R3.1"]`) and produces one or more
`Track` entries connecting them. The router pulls coordinates from the
pad positions in the project's existing `pcb.footprints`. For now,
routing is simple: emit a single Manhattan track per pair of
consecutive waypoints. The richer walk-around router from M2-R-08
will replace the geometry pass; the MCP surface stays stable.
"""

from __future__ import annotations

import uuid
from itertools import pairwise
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post


@tool(
    "kc_track_route",
    "Route a net through one or more waypoint pads. Waypoints are "
    '`refdes.pad` strings (e.g. "U1.7"). Returns the list of created '
    "track uuids. Routing geometry is the M2 walk-around router; for "
    "the M2-P-04 boot, we emit straight-line Manhattan segments — the "
    "router stays under the same tool surface as it lands.",
    {
        "project_id": str,
        "net": str,
        "waypoints": list[str],
        "layer": str,
        "width_mm": float,
    },
)
async def kc_track_route(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    net = (args.get("net") or "").strip()
    waypoints = args.get("waypoints") or []
    if not project_id or not net:
        return error_envelope("`project_id` and `net` are required")
    if not isinstance(waypoints, list) or len(waypoints) < 2:
        return error_envelope("`waypoints` must contain at least two refdes.pad entries")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    pcb = project.setdefault("pcb", {})
    layer = (args.get("layer") or "F.Cu").strip()
    width_mm = float(args.get("width_mm") or _default_width_for(pcb, net) or 0.25)

    coords: list[tuple[float, float]] = []
    unresolved: list[str] = []
    for wp in waypoints:
        xy = _resolve_pad(pcb, str(wp))
        if xy is None:
            unresolved.append(str(wp))
            continue
        coords.append(xy)
    if unresolved:
        return error_envelope(
            f"could not resolve waypoints {unresolved}",
            project_id=project_id,
            net=net,
        )

    tracks = pcb.setdefault("tracks", [])
    created: list[str] = []
    for (sx, sy), (ex, ey) in pairwise(coords):
        # Manhattan: drop one corner so the track has a horizontal then
        # a vertical leg. Use the midpoint x. This is intentionally
        # naïve — the M2-R-08 walk-around router supplants this.
        midx = (sx + ex) / 2.0
        track_uuid = str(uuid.uuid4())
        tracks.append(
            {
                "uuid": track_uuid,
                "layer": layer,
                "net": net,
                "points_mm": [[sx, sy], [midx, sy], [midx, ey], [ex, ey]],
                "width_mm": width_mm,
                "locked": False,
            }
        )
        created.append(track_uuid)

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
            "net": net,
            "layer": layer,
            "width_mm": width_mm,
            "track_uuids": created,
        }
    )


@tool(
    "kc_track_remove",
    "Remove one or more tracks by uuid (or every track on a named net).",
    {
        "project_id": str,
        "track_uuids": list[str],
        "net": str,
    },
)
async def kc_track_remove(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    uuids = args.get("track_uuids") or []
    net = (args.get("net") or "").strip()
    if not project_id or (not uuids and not net):
        return error_envelope("`project_id` plus either `track_uuids` or `net` is required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    pcb = project.setdefault("pcb", {})
    tracks = pcb.setdefault("tracks", [])
    uuids_set = {str(u) for u in uuids}
    keep: list[dict[str, Any]] = []
    removed: list[str] = []
    for t in tracks:
        if (uuids_set and t.get("uuid") in uuids_set) or (net and t.get("net") == net):
            removed.append(t.get("uuid", ""))
            continue
        keep.append(t)
    pcb["tracks"] = keep
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
            "removed_uuids": removed,
        }
    )


# ---------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------


def _resolve_pad(pcb: dict[str, Any], waypoint: str) -> tuple[float, float] | None:
    """Resolve `"U1.7"` to the absolute pad position on the board."""
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
        # No pad found — fall back to the footprint origin.
        return (fx, fy)
    return None


def _default_width_for(pcb: dict[str, Any], net: str) -> float | None:
    """Look up a default trace width from the net's class. Returns
    `None` if the net or its class is missing — caller picks a fallback.
    """
    nets = pcb.get("nets", []) or []
    class_name = ""
    for n in nets:
        if n.get("name") == net:
            cls = n.get("class")
            if isinstance(cls, dict):
                class_name = str(cls.get("0") or "")
            elif isinstance(cls, str):
                class_name = cls
            elif isinstance(cls, list) and cls:
                class_name = str(cls[0])
            break
    if not class_name:
        return None
    for nc in pcb.get("net_classes", []) or []:
        if nc.get("name") == class_name:
            try:
                return float(nc.get("trace_width_mm") or 0.0) or None
            except (TypeError, ValueError):
                return None
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


__all__ = ["kc_track_remove", "kc_track_route"]
