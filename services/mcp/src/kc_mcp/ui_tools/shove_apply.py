"""`ui_shove_apply` — persist a push-and-shove route (M3-T-05).

The React `useShoveRoute` gesture runs the M3-R-03 push-and-shove
router live in wasm and, when the user commits, calls this tool with
the plan:

- `new_track` — the freshly-routed trace to add to `pcb.tracks`.
- `moved_tracks` — every existing track the shove displaced, keyed by
  its project `uuid` with its new `points_mm`.

This tool applies both atomically against the in-memory project dict:
the new track is appended with a fresh uuid; each moved track's
`points_mm` is replaced in place by uuid. A moved-track uuid that
isn't on the board is a no-op for that entry (the gesture may have
been built against a since-deleted track) — surfaced in the response's
`unmatched` list rather than failing the whole apply.

Mirrors the `ui_*` pattern: takes the project dict + kwargs, returns
`{ok, ...}` and the mutated `project` for kiserver's `replace`.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any


def _coerce_points(raw: Any) -> list[list[float]] | None:
    """Normalise a points list into `[[x, y], ...]`. Accepts the
    `[[x, y]]` array form the wasm bridge sends. Returns None when the
    shape is wrong."""
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    out: list[list[float]] = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            try:
                out.append([float(entry[0]), float(entry[1])])
            except (TypeError, ValueError):
                return None
        elif isinstance(entry, dict) and "x" in entry and "y" in entry:
            try:
                out.append([float(entry["x"]), float(entry["y"])])
            except (TypeError, ValueError):
                return None
        else:
            return None
    return out


def ui_shove_apply(
    project: dict[str, Any],
    *,
    new_track: dict[str, Any] | None = None,
    moved_tracks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(new_track, dict):
        return {"ok": False, "error": "`new_track` is required"}
    new_points = _coerce_points(new_track.get("points_mm"))
    if new_points is None:
        return {
            "ok": False,
            "error": "`new_track.points_mm` must be a list of ≥ 2 [x, y] points",
        }
    net = str(new_track.get("net") or "")
    layer = str(new_track.get("layer") or "")
    if not net or not layer:
        return {"ok": False, "error": "`new_track` requires `net` and `layer`"}
    try:
        width_mm = float(new_track.get("width_mm") or 0.0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "`new_track.width_mm` must be a number"}
    if width_mm <= 0:
        return {"ok": False, "error": "`new_track.width_mm` must be > 0"}

    pcb = project.setdefault("pcb", {})
    tracks: list[dict[str, Any]] = pcb.setdefault("tracks", [])

    # Apply the moved tracks first (so a failure there doesn't leave a
    # dangling new track).
    moved = moved_tracks or []
    by_uuid = {t.get("uuid"): t for t in tracks if isinstance(t, dict)}
    updated: list[str] = []
    unmatched: list[str] = []
    for entry in moved:
        if not isinstance(entry, dict):
            return {"ok": False, "error": "each `moved_tracks` entry must be an object"}
        track_uuid = entry.get("uuid")
        if not track_uuid:
            return {"ok": False, "error": "each `moved_tracks` entry needs a `uuid`"}
        pts = _coerce_points(entry.get("points_mm"))
        if pts is None:
            return {
                "ok": False,
                "error": f"moved track {track_uuid!r}: points_mm must be ≥ 2 [x, y] points",
            }
        target = by_uuid.get(track_uuid)
        if target is None:
            unmatched.append(str(track_uuid))
            continue
        target["points_mm"] = pts
        updated.append(str(track_uuid))

    # Append the new track with a fresh uuid.
    new_uuid = uuid_mod.uuid4().hex
    new_entry = {
        "uuid": new_uuid,
        "net": net,
        "layer": layer,
        "width_mm": width_mm,
        "points_mm": new_points,
        "locked": False,
    }
    tracks.append(new_entry)

    return {
        "ok": True,
        "new_track_uuid": new_uuid,
        "updated_track_uuids": updated,
        "unmatched_track_uuids": unmatched,
        "project": project,
    }


__all__ = ["ui_shove_apply"]
