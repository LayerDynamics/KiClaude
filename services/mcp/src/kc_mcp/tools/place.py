"""`kc_footprint_place_hint` + `kc_footprint_remove` — declarative
footprint placement on the PCB (M2-P-04).

Per SPEC §1.4 #4, the Claude-facing footprint tools take **hints**,
not raw coordinates. The frontend gets raw-coordinate variants under
`ui_tools/`. This module resolves hints like:

- `anchor_refdes` — place near an already-placed footprint
- `edge` — `north`/`south`/`east`/`west` board edge
- `cluster` — group with footprints sharing this refdes prefix

The resolver picks a coordinate using the current board outline +
already-placed footprints; it deliberately produces a single best-effort
position and reports the hint set that drove it so the user can see
the reasoning chain.
"""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get, kiserver_post


@tool(
    "kc_footprint_place_hint",
    "Place a footprint on the PCB using declarative hints. "
    "Hints supported: anchor_refdes (place near an existing refdes), "
    "edge (north/south/east/west of the board outline), "
    "cluster (group with refdes-prefix peers), offset_mm. "
    "Returns the placed footprint's uuid + the resolved coordinate. "
    "Spec §1.4 #4 — Claude never sees raw xy.",
    {
        "project_id": str,
        "refdes": str,
        "lib_id": str,
        "value": str,
        "mpn": str,
        "layer": str,
        "rotation_deg": float,
        "anchor_refdes": str,
        "edge": str,
        "cluster": str,
        "offset_mm": list[float],
    },
)
async def kc_footprint_place_hint(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    refdes = (args.get("refdes") or "").strip()
    lib_id = args.get("lib_id", "")
    if not project_id or not lib_id:
        return error_envelope("`project_id` and `lib_id` are required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )

    pcb = project.setdefault("pcb", {})
    footprints = pcb.setdefault("footprints", [])
    position, reasoning = _resolve_position(
        pcb,
        anchor_refdes=args.get("anchor_refdes") or "",
        edge=(args.get("edge") or "").lower(),
        cluster=(args.get("cluster") or "").strip(),
        offset_mm=args.get("offset_mm"),
    )
    fp_uuid = str(uuid.uuid4())
    layer = (args.get("layer") or "F.Cu").strip()
    new_fp = {
        "uuid": fp_uuid,
        "refdes": refdes,
        "lib_id": lib_id,
        "value": (args.get("value") or "").strip(),
        "mpn": (args.get("mpn") or "").strip(),
        "layer": layer,
        "position_mm": list(position),
        "rotation_deg": float(args.get("rotation_deg") or 0.0),
        "locked": False,
        "attributes": [],
        "pads": [],
        "courtyard": None,
        "models_3d": [],
        "drawings": [],
    }
    footprints.append(new_fp)
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
            "footprint_uuid": fp_uuid,
            "refdes": refdes,
            "lib_id": lib_id,
            "layer": layer,
            "resolved_position_mm": list(position),
            "reasoning": reasoning,
        }
    )


@tool(
    "kc_footprint_remove",
    "Remove a footprint from the PCB by uuid or refdes. Returns the "
    "removed footprint's uuid + refdes so an undo / activity-journal "
    "entry can recreate it.",
    {
        "project_id": str,
        "footprint_uuid": str,
        "refdes": str,
    },
)
async def kc_footprint_remove(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    fp_uuid = (args.get("footprint_uuid") or "").strip()
    refdes = (args.get("refdes") or "").strip()
    if not project_id or (not fp_uuid and not refdes):
        return error_envelope("`project_id` plus either `footprint_uuid` or `refdes` is required")
    project = await _fetch_project(project_id)
    if project is None:
        return error_envelope(
            f"kiserver could not return project_id={project_id}",
            project_id=project_id,
        )
    pcb = project.setdefault("pcb", {})
    footprints = pcb.setdefault("footprints", [])
    target_index: int | None = None
    target_fp: dict[str, Any] | None = None
    for i, fp in enumerate(footprints):
        if fp_uuid and fp.get("uuid") == fp_uuid:
            target_index, target_fp = i, fp
            break
        if refdes and fp.get("refdes") == refdes:
            target_index, target_fp = i, fp
            break
    if target_index is None or target_fp is None:
        return error_envelope(
            "no matching footprint found",
            project_id=project_id,
            footprint_uuid=fp_uuid,
            refdes=refdes,
        )
    removed = footprints.pop(target_index)
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
            "removed_uuid": removed.get("uuid", ""),
            "removed_refdes": removed.get("refdes", ""),
        }
    )


# ---------------------------------------------------------------------
# Hint resolution.
# ---------------------------------------------------------------------


def _resolve_position(
    pcb: dict[str, Any],
    *,
    anchor_refdes: str,
    edge: str,
    cluster: str,
    offset_mm: Any,
) -> tuple[tuple[float, float], list[str]]:
    """Pick a coordinate using the supplied hints.

    Resolution order:

    1. `anchor_refdes`: place at `(anchor.position + offset_mm or (5, 0))`.
    2. `cluster`: average position of footprints whose refdes shares
       the same alpha prefix as `cluster`, plus a small grid step.
    3. `edge`: midpoint of the named edge of the board outline.
    4. Fallback: the board outline centroid (or (50, 50) if no outline).

    Returns `(position_mm, reasoning_list)` so the response envelope
    can show its work.
    """
    reasoning: list[str] = []
    offset = _as_xy(offset_mm) or (5.0, 0.0)
    if anchor_refdes:
        anchor = _find_footprint(pcb, refdes=anchor_refdes)
        if anchor is not None:
            ax, ay = _as_xy(anchor.get("position_mm")) or (0.0, 0.0)
            pos = (ax + offset[0], ay + offset[1])
            reasoning.append(
                f"placed at ({pos[0]:.3f},{pos[1]:.3f}) — offset from anchor {anchor_refdes}"
            )
            return pos, reasoning
        reasoning.append(f"anchor_refdes {anchor_refdes!r} not found; falling through")

    if cluster:
        peers = _cluster_peers(pcb, cluster)
        if peers:
            avg_x = sum(p[0] for p in peers) / len(peers)
            avg_y = sum(p[1] for p in peers) / len(peers)
            # Grid-step away from the centroid so we don't stack on a peer.
            pos = (avg_x + 2.54 * len(peers), avg_y)
            reasoning.append(
                f"placed at ({pos[0]:.3f},{pos[1]:.3f}) — "
                f"clustered with {len(peers)} peers of {cluster!r}"
            )
            return pos, reasoning
        reasoning.append(f"cluster {cluster!r}: no peers; falling through")

    if edge in {"north", "south", "east", "west"}:
        pos = _edge_midpoint(pcb, edge)
        reasoning.append(f"placed at ({pos[0]:.3f},{pos[1]:.3f}) — midpoint of {edge} edge")
        return pos, reasoning

    centroid = _outline_centroid(pcb)
    reasoning.append(
        f"placed at ({centroid[0]:.3f},{centroid[1]:.3f}) — outline centroid (fallback)"
    )
    return centroid, reasoning


def _find_footprint(pcb: dict[str, Any], *, refdes: str) -> dict[str, Any] | None:
    for fp in pcb.get("footprints", []) or []:
        if fp.get("refdes") == refdes:
            return fp
    return None


def _cluster_peers(pcb: dict[str, Any], cluster: str) -> list[tuple[float, float]]:
    prefix = "".join(c for c in cluster if c.isalpha()).upper()
    out: list[tuple[float, float]] = []
    for fp in pcb.get("footprints", []) or []:
        rd = (fp.get("refdes") or "").upper()
        rd_prefix = "".join(c for c in rd if c.isalpha())
        if rd_prefix == prefix:
            xy = _as_xy(fp.get("position_mm"))
            if xy is not None:
                out.append(xy)
    return out


def _edge_midpoint(pcb: dict[str, Any], edge: str) -> tuple[float, float]:
    pts = _outline_points(pcb)
    if not pts:
        return (50.0, 50.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    midx = (minx + maxx) / 2.0
    midy = (miny + maxy) / 2.0
    margin = 2.0
    if edge == "north":
        return (midx, miny + margin)
    if edge == "south":
        return (midx, maxy - margin)
    if edge == "west":
        return (minx + margin, midy)
    return (maxx - margin, midy)


def _outline_centroid(pcb: dict[str, Any]) -> tuple[float, float]:
    pts = _outline_points(pcb)
    if not pts:
        return (50.0, 50.0)
    sx = sum(p[0] for p in pts) / len(pts)
    sy = sum(p[1] for p in pts) / len(pts)
    return (sx, sy)


def _outline_points(pcb: dict[str, Any]) -> list[tuple[float, float]]:
    outline = pcb.get("outline") or {}
    raw = outline.get("points_mm") or []
    out: list[tuple[float, float]] = []
    for p in raw:
        xy = _as_xy(p)
        if xy is not None:
            out.append(xy)
    return out


def _as_xy(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError, IndexError):
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


__all__ = ["kc_footprint_place_hint", "kc_footprint_remove"]
