"""`ui_netclass_set` + `ui_netclass_delete` — UI-only net-class
mutations (M2-P-05 extension for M2-T-07).

The Claude-facing `kc_netclass_set` requires a `project_id` and
round-trips through kiserver's `/replace`. The PCB editor's
NetClassPanel mutates the in-memory project directly through the
gateway and lets kiserver persist the result via its own replace
endpoint — no Claude involvement, matching the rest of the UI tool
family.

These functions take the in-memory project dict and mutate the
`pcb.net_classes` list. They return the updated class + the list of
nets that are bound to it.
"""

from __future__ import annotations

from typing import Any


def _default_netclass(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": "",
        "clearance_mm": 0.2,
        "trace_width_mm": 0.25,
        "via_drill_mm": 0.3,
        "via_diameter_mm": 0.6,
        "diff_pair_width_mm": None,
        "diff_pair_gap_mm": None,
    }


def ui_netclass_set(
    project: dict[str, Any],
    *,
    name: str,
    description: str | None = None,
    clearance_mm: float | None = None,
    trace_width_mm: float | None = None,
    via_drill_mm: float | None = None,
    via_diameter_mm: float | None = None,
    diff_pair_width_mm: float | None = None,
    diff_pair_gap_mm: float | None = None,
    bind_nets: list[str] | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "`name` is required"}
    pcb = project.setdefault("pcb", {})
    classes: list[dict[str, Any]] = pcb.setdefault("net_classes", [])
    existing = next((c for c in classes if c.get("name") == name), None)
    if existing is None:
        existing = _default_netclass(name)
        classes.append(existing)

    if description is not None:
        existing["description"] = str(description)
    for key, value in (
        ("clearance_mm", clearance_mm),
        ("trace_width_mm", trace_width_mm),
        ("via_drill_mm", via_drill_mm),
        ("via_diameter_mm", via_diameter_mm),
        ("diff_pair_width_mm", diff_pair_width_mm),
        ("diff_pair_gap_mm", diff_pair_gap_mm),
    ):
        if value is not None:
            existing[key] = float(value)

    bound: list[str] = []
    if bind_nets:
        target = {n.strip() for n in bind_nets if n and n.strip()}
        nets = pcb.setdefault("nets", [])
        for net in nets:
            if net.get("name") in target:
                # Mirrors `kc_netclass_set`'s wire format —
                # `Net.class = [name]` so kiserver's parser
                # roundtrips correctly.
                net["class"] = [name]
                bound.append(str(net["name"]))
    return {
        "ok": True,
        "net_class": existing,
        "bound_nets": bound,
        "project": project,
    }


def ui_netclass_delete(
    project: dict[str, Any],
    *,
    name: str,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "`name` is required"}
    if name == "Default":
        return {
            "ok": False,
            "error": "the `Default` net class cannot be deleted",
        }
    pcb = project.setdefault("pcb", {})
    classes: list[dict[str, Any]] = pcb.get("net_classes") or []
    new_classes = [c for c in classes if c.get("name") != name]
    if len(new_classes) == len(classes):
        return {
            "ok": False,
            "error": f"net class {name!r} not found",
        }
    pcb["net_classes"] = new_classes
    # Any nets bound to the deleted class fall back to `Default`.
    unbound: list[str] = []
    for net in pcb.get("nets", []):
        cls = net.get("class")
        bound_name = (
            cls[0] if isinstance(cls, list) and cls else (cls if isinstance(cls, str) else None)
        )
        if bound_name == name:
            net["class"] = ["Default"]
            unbound.append(str(net["name"]))
    return {
        "ok": True,
        "deleted": name,
        "unbound_nets": unbound,
        "project": project,
    }


__all__ = ["ui_netclass_delete", "ui_netclass_set"]
