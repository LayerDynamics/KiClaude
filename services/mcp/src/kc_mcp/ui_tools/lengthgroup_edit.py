"""`ui_lengthgroup_set` + `ui_lengthgroup_delete` — UI-only length-match
group mutations (M3-T-04).

Length-match groups live as `project["pcb"]["length_groups"]` per KCIR
(`crates/ki/src/kcir/lengthgroup.rs`). They drive the M3-R-05
analyzer's tuning queue. The React `LengthMatchPanel` mutates the
in-memory project's `pcb.length_groups` list directly through the
gateway, mirroring the `ui_diffpair_set` pattern.

Identity is by `name`. Validation enforces:

- `name` is non-empty;
- `nets` is a non-empty list (a group with no members is
  meaningless — block the no-op rather than silently saving it);
- every member net exists on `pcb.nets` (skip-validation = the
  analyzer would silently report them as Unrouted, masking typos);
- `target_length_mm ≥ 0` (0 = "match the longest", per the
  analyzer);
- `tolerance_mm ≥ 0`;
- net names are unique within the group (a typo'd duplicate
  would skew the analyzer's longest-wins picker).

Net→group membership is a many-to-many relation: a net can sit in
multiple groups (e.g. DDR DQS pair lives in both `DDR_DQS_BYTE0`
and `DDR_CLK_GROUP`). We don't enforce single-membership on the
backend.
"""

from __future__ import annotations

from typing import Any


def _default_group(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "nets": [],
        "target_length_mm": 0.0,
        "tolerance_mm": 0.127,  # 5 mil — diff-pair default; user overrides
    }


def ui_lengthgroup_set(
    project: dict[str, Any],
    *,
    name: str,
    nets: list[str] | None = None,
    target_length_mm: float | None = None,
    tolerance_mm: float | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "`name` is required"}
    pcb = project.setdefault("pcb", {})
    groups: list[dict[str, Any]] = pcb.setdefault("length_groups", [])
    existing = next((g for g in groups if g.get("name") == name), None)
    previous = dict(existing) if existing else None

    if existing is None:
        if not nets:
            return {
                "ok": False,
                "error": "`nets` is required (at least one member) when creating a group",
            }
        existing = _default_group(name)
        groups.append(existing)

    if nets is not None:
        if not isinstance(nets, list):
            if previous is None:
                groups.pop()
            else:
                existing.update(previous)
            return {"ok": False, "error": "`nets` must be a list of net names"}
        normalised: list[str] = []
        seen: set[str] = set()
        for raw in nets:
            net_name = str(raw).strip()
            if not net_name:
                continue
            if net_name in seen:
                if previous is None:
                    groups.pop()
                else:
                    existing.update(previous)
                return {
                    "ok": False,
                    "error": f"duplicate net {net_name!r} in group {name!r}",
                }
            seen.add(net_name)
            normalised.append(net_name)
        if not normalised:
            if previous is None:
                groups.pop()
            else:
                existing.update(previous)
            return {
                "ok": False,
                "error": "`nets` must contain at least one non-empty entry",
            }
        # Cross-check against the board's known nets so a typo can't
        # land silently and become a phantom Unrouted row in the
        # analyzer report.
        board_nets = {
            str(n.get("name", "")) for n in (pcb.get("nets") or [])
        }
        for net_name in normalised:
            if net_name not in board_nets:
                if previous is None:
                    groups.pop()
                else:
                    existing.update(previous)
                return {
                    "ok": False,
                    "error": f"length group {name!r}: net {net_name!r} not found on this board",
                }
        existing["nets"] = normalised

    for key, value in (
        ("target_length_mm", target_length_mm),
        ("tolerance_mm", tolerance_mm),
    ):
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            if previous is None:
                groups.pop()
            else:
                existing.update(previous)
            return {"ok": False, "error": f"`{key}` must be a number"}
        if numeric < 0:
            if previous is None:
                groups.pop()
            else:
                existing.update(previous)
            return {"ok": False, "error": f"`{key}` must be ≥ 0"}
        existing[key] = numeric

    return {
        "ok": True,
        "length_group": existing,
        "project": project,
    }


def ui_lengthgroup_delete(
    project: dict[str, Any],
    *,
    name: str,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "`name` is required"}
    pcb = project.setdefault("pcb", {})
    groups: list[dict[str, Any]] = pcb.get("length_groups") or []
    victim = next((g for g in groups if g.get("name") == name), None)
    if victim is None:
        return {"ok": False, "error": f"length group {name!r} not found"}
    pcb["length_groups"] = [g for g in groups if g is not victim]
    return {
        "ok": True,
        "deleted": name,
        "project": project,
    }


__all__ = ["ui_lengthgroup_delete", "ui_lengthgroup_set"]
