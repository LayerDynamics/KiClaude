"""`ui_diffpair_set` + `ui_diffpair_delete` — UI-only differential-pair
declaration mutations (M3-T-03).

Diff pairs live as `project["pcb"]["diff_pairs"]` per KCIR
(`crates/ki/src/kcir/diffpair.rs`). The Claude-facing surface for
declaring pairs goes through declarative hints; the React
`DiffPairPanel` mutates the in-memory project's `pcb.diff_pairs` list
directly through the gateway, mirroring the `ui_netclass_set` pattern.

Identity is by `name`: upsert by name, delete by name. Validation
enforces:

- both nets exist on `pcb.nets`;
- `net_positive` ≠ `net_negative`;
- the same `(net_positive, net_negative)` pair isn't already
  declared under a different name (per KiCad, a net can only be one
  side of one pair at a time);
- `target_impedance_ohms` and `target_gap_mm` are ≥ 0 (`0.0` means
  "unspecified" — solver picks);
- `skew_tolerance_mm` ≥ 0.

The mutation also propagates a back-reference into each leg's
`Net.diff_pair` slot so a schematic-side view that reads
`Net.diff_pair_with` sees the link without having to scan
`pcb.diff_pairs` separately.
"""

from __future__ import annotations

from typing import Any


def _default_diffpair(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "net_positive": "",
        "net_negative": "",
        "target_impedance_ohms": 0.0,
        "target_gap_mm": 0.0,
        "length_group": "",
        "skew_tolerance_mm": 0.0,
    }


def _existing_net_names(project: dict[str, Any]) -> set[str]:
    pcb = project.get("pcb") or {}
    return {str(n.get("name", "")) for n in (pcb.get("nets") or [])}


def _ensure_pair_back_refs(
    pcb: dict[str, Any], pair: dict[str, Any], previous: dict[str, Any] | None
) -> None:
    """Maintain the `Net.diff_pair` back-reference. Clears any prior
    back-refs the pair pointed at (so renaming legs unlinks the old
    nets), then sets fresh ones."""
    targets: set[str] = set()
    if previous is not None:
        targets.update({previous.get("net_positive", ""), previous.get("net_negative", "")})
    targets.update({pair["net_positive"], pair["net_negative"]})
    targets.discard("")
    nets = pcb.setdefault("nets", [])
    new_positive = pair["net_positive"]
    new_negative = pair["net_negative"]
    for net in nets:
        name = str(net.get("name", ""))
        if name not in targets:
            continue
        if name == new_positive:
            net["diff_pair"] = new_negative
        elif name == new_negative:
            net["diff_pair"] = new_positive
        else:
            # Used to be in this pair but no longer is — drop the link.
            net["diff_pair"] = None


def ui_diffpair_set(
    project: dict[str, Any],
    *,
    name: str,
    net_positive: str | None = None,
    net_negative: str | None = None,
    target_impedance_ohms: float | None = None,
    target_gap_mm: float | None = None,
    length_group: str | None = None,
    skew_tolerance_mm: float | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "`name` is required"}
    pcb = project.setdefault("pcb", {})
    pairs: list[dict[str, Any]] = pcb.setdefault("diff_pairs", [])
    existing = next((p for p in pairs if p.get("name") == name), None)
    previous = dict(existing) if existing else None

    if existing is None:
        if not net_positive or not net_negative:
            return {
                "ok": False,
                "error": "`net_positive` and `net_negative` are required when creating a pair",
            }
        existing = _default_diffpair(name)
        pairs.append(existing)

    if net_positive is not None:
        existing["net_positive"] = str(net_positive).strip()
    if net_negative is not None:
        existing["net_negative"] = str(net_negative).strip()
    if existing["net_positive"] == existing["net_negative"] and existing["net_positive"]:
        # Roll back the insert — never leave a degenerate pair behind.
        if previous is None:
            pairs.pop()
        else:
            existing.update(previous)
        return {
            "ok": False,
            "error": f"diff pair {name!r} cannot have the same net on both legs",
        }

    nets = _existing_net_names(project)
    for leg_key in ("net_positive", "net_negative"):
        net_name = existing[leg_key]
        if net_name and net_name not in nets:
            if previous is None:
                pairs.pop()
            else:
                existing.update(previous)
            return {
                "ok": False,
                "error": f"diff pair {name!r}: net {net_name!r} not found on this board",
            }

    # Disallow duplicate pair declarations (matches KiCad: a net is
    # one half of at most one pair at a time).
    for other in pairs:
        if other is existing:
            continue
        legs = {other.get("net_positive"), other.get("net_negative")}
        if existing["net_positive"] and existing["net_positive"] in legs:
            if previous is None:
                pairs.pop()
            else:
                existing.update(previous)
            return {
                "ok": False,
                "error": (
                    f"net {existing['net_positive']!r} is already declared in pair "
                    f"{other.get('name')!r}"
                ),
            }
        if existing["net_negative"] and existing["net_negative"] in legs:
            if previous is None:
                pairs.pop()
            else:
                existing.update(previous)
            return {
                "ok": False,
                "error": (
                    f"net {existing['net_negative']!r} is already declared in pair "
                    f"{other.get('name')!r}"
                ),
            }

    for key, value in (
        ("target_impedance_ohms", target_impedance_ohms),
        ("target_gap_mm", target_gap_mm),
        ("skew_tolerance_mm", skew_tolerance_mm),
    ):
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            if previous is None:
                pairs.pop()
            else:
                existing.update(previous)
            return {"ok": False, "error": f"`{key}` must be a number"}
        if numeric < 0:
            if previous is None:
                pairs.pop()
            else:
                existing.update(previous)
            return {"ok": False, "error": f"`{key}` must be ≥ 0"}
        existing[key] = numeric
    if length_group is not None:
        existing["length_group"] = str(length_group)

    _ensure_pair_back_refs(pcb, existing, previous)

    return {
        "ok": True,
        "diff_pair": existing,
        "project": project,
    }


def ui_diffpair_delete(
    project: dict[str, Any],
    *,
    name: str,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "`name` is required"}
    pcb = project.setdefault("pcb", {})
    pairs: list[dict[str, Any]] = pcb.get("diff_pairs") or []
    victim = next((p for p in pairs if p.get("name") == name), None)
    if victim is None:
        return {"ok": False, "error": f"diff pair {name!r} not found"}
    new_pairs = [p for p in pairs if p is not victim]
    pcb["diff_pairs"] = new_pairs
    # Clear Net.diff_pair back-refs on the orphaned legs.
    cleared: list[str] = []
    targets = {victim.get("net_positive"), victim.get("net_negative")}
    targets.discard(None)
    targets.discard("")
    for net in pcb.get("nets", []):
        if net.get("name") in targets and net.get("diff_pair") is not None:
            net["diff_pair"] = None
            cleared.append(str(net["name"]))
    return {
        "ok": True,
        "deleted": name,
        "cleared_back_refs": cleared,
        "project": project,
    }


__all__ = ["ui_diffpair_delete", "ui_diffpair_set"]
