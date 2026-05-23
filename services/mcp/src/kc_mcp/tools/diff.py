"""`kc_diff` — structural diff between two KCIR projects (M2-P-04).

Produces a JSON delta the M2-T-11 `kiclaude diff` CLI and the future
`/pcb-review` command can render. Diffs include footprint
adds/removes/moves, track add/remove, zone add/remove, net-class
edits, and the bag of "value changed" string-field deltas on
existing footprints.

The tool is **pure**: it takes two project dicts and returns the
delta. It does not touch the kiserver — the caller is expected to
have fetched the two snapshots themselves.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope


@tool(
    "kc_diff",
    "Structural diff between two KCIR project snapshots. Returns "
    "{ok, added:[...], removed:[...], modified:[...]} grouped by "
    "section (footprints, tracks, vias, zones, nets, net_classes). "
    "Pure function — does not mutate state.",
    {
        "before": dict,
        "after": dict,
        "section": str,
    },
)
async def kc_diff(args: dict[str, Any]) -> dict[str, Any]:
    before = args.get("before")
    after = args.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return error_envelope("`before` and `after` must be KCIR project dicts")
    section = (args.get("section") or "").strip().lower()
    delta = diff_projects(before, after)
    if section:
        if section not in delta:
            return error_envelope(f"unknown section {section!r}; valid: {sorted(delta)}")
        delta = {section: delta[section]}
    return envelope({"ok": True, **delta})


def diff_projects(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compute the per-section delta between two KCIR projects.

    Returns a dict keyed by section name; each section carries
    `{added, removed, modified}` lists.
    """
    out: dict[str, Any] = {}
    pcb_before = before.get("pcb") or {}
    pcb_after = after.get("pcb") or {}
    out["footprints"] = _diff_by_key(
        pcb_before.get("footprints") or [],
        pcb_after.get("footprints") or [],
        key="uuid",
        compare_fields=("refdes", "value", "mpn", "layer", "position_mm", "rotation_deg"),
    )
    out["tracks"] = _diff_by_key(
        pcb_before.get("tracks") or [],
        pcb_after.get("tracks") or [],
        key="uuid",
        compare_fields=("layer", "net", "width_mm", "points_mm", "locked"),
    )
    out["vias"] = _diff_by_key(
        pcb_before.get("vias") or [],
        pcb_after.get("vias") or [],
        key="uuid",
        compare_fields=(
            "net",
            "position_mm",
            "drill_mm",
            "diameter_mm",
            "from_layer",
            "to_layer",
            "kind",
            "locked",
        ),
    )
    out["zones"] = _diff_by_key(
        pcb_before.get("zones") or [],
        pcb_after.get("zones") or [],
        key="uuid",
        compare_fields=("layer", "net", "outline_mm", "thermal_relief", "hatched", "connect_pads"),
    )
    out["nets"] = _diff_by_key(
        pcb_before.get("nets") or [],
        pcb_after.get("nets") or [],
        key="name",
        compare_fields=("class",),
    )
    out["net_classes"] = _diff_by_key(
        pcb_before.get("net_classes") or [],
        pcb_after.get("net_classes") or [],
        key="name",
        compare_fields=(
            "clearance_mm",
            "trace_width_mm",
            "via_drill_mm",
            "via_diameter_mm",
            "diff_pair_width_mm",
            "diff_pair_gap_mm",
        ),
    )
    return out


def _diff_by_key(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    key: str,
    compare_fields: tuple[str, ...],
) -> dict[str, Any]:
    by_before: dict[str, dict[str, Any]] = {
        str(item.get(key, "")): item for item in before if item.get(key) is not None
    }
    by_after: dict[str, dict[str, Any]] = {
        str(item.get(key, "")): item for item in after if item.get(key) is not None
    }
    added_keys = sorted(set(by_after) - set(by_before))
    removed_keys = sorted(set(by_before) - set(by_after))
    modified: list[dict[str, Any]] = []
    for k in sorted(set(by_before) & set(by_after)):
        diffs: dict[str, Any] = {}
        for f in compare_fields:
            if by_before[k].get(f) != by_after[k].get(f):
                diffs[f] = {"before": by_before[k].get(f), "after": by_after[k].get(f)}
        if diffs:
            modified.append({key: k, "changes": diffs})
    return {
        "added": [by_after[k] for k in added_keys],
        "removed": [by_before[k] for k in removed_keys],
        "modified": modified,
    }


__all__ = ["diff_projects", "kc_diff"]
