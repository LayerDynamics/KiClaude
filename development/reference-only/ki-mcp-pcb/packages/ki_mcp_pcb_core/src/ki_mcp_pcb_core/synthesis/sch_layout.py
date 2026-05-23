"""Schematic auto-layout — pure-data placement algorithm.

What "auto-layout" means here:

  * Group components by **connectivity** on non-power, non-ground nets.
    Things that share a signal go in the same visual cluster.
  * Group decoupling capacitors with the IC that owns them — a cap that
    bridges an IC's declared supply rail and ground is "this IC's
    decoupler" and gets placed in a halo around the IC.
  * Order clusters left-to-right by **partition** (analog → digital → rf
    → power → mixed) so mixed-signal boards have an obvious left/right
    split.
  * Inside a cluster, the IC sits at the center; decouplers ring it;
    other passives fall in concentric arcs further out.

This is just placement math — no KiCad coupling. The synthesis layer
applies the resulting positions when emitting ``.kicad_sch``. Real
visual schematic quality is still constrained by the fact that we
don't have access to symbol pin coordinates without the KiCad
libraries; this gets the structure right so KiCad's
"Annotate / Reorganize" pass produces something readable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ki_mcp_pcb_core.cir.models import Board, Component, Net

# Canvas-space defaults. mm.
_CLUSTER_GAP_MM = 50.0   # Between sibling clusters on the page
_CLUSTER_MARGIN_MM = 40.0  # Page edge → first cluster
_RADIUS_MM = 25.4         # Halo radius around an IC for decouplers
_PASSIVE_STEP_MM = 12.7   # Step between non-decoupler passives in a cluster
_PAGE_TOP_MM = 50.0


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


# Net classes that don't define meaningful clustering edges.
_GLOBAL_NET_CLASSES = frozenset({"ground", "power"})

# Partition ordering on the page (left → right).
_PARTITION_ORDER = ("analog", "rf", "digital", "power", "isolated", None)


@dataclass(frozen=True)
class LayoutPlacement:
    refdes: str
    x_mm: float
    y_mm: float
    cluster: str        # cluster id (typically the parent-IC refdes or a synthetic group key)
    role: str           # "ic" | "decoupler" | "passive" | "connector" | "free"


# ---------------------------------------------------------------------------
# Phase 1 — clustering
# ---------------------------------------------------------------------------


def cluster_components(board: Board) -> dict[str, list[str]]:
    """Group components by shared non-power-non-ground nets.

    Returns ``{cluster_id: [refdes, ...]}``. The cluster id is the
    "anchor" component's refdes — preferentially an IC (``U*``), else
    the first node in the connected component.
    """
    refdes_set = {c.refdes for c in board.components}
    # Adjacency on non-global nets.
    adj: dict[str, set[str]] = {r: set() for r in refdes_set}
    for net in board.nets:
        if net.net_class in _GLOBAL_NET_CLASSES:
            continue
        members = sorted({m.split(".", 1)[0] for m in net.members if m.split(".", 1)[0] in refdes_set})
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                adj[a].add(b)
                adj[b].add(a)

    # Union-find over the connectivity graph.
    parent = {r: r for r in refdes_set}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, neighbors in adj.items():
        for b in neighbors:
            union(a, b)

    # Buckets by connected component, then choose anchor.
    buckets: dict[str, list[str]] = {}
    for r in refdes_set:
        root = find(r)
        buckets.setdefault(root, []).append(r)

    # Rename each cluster by its best anchor.
    out: dict[str, list[str]] = {}
    for members in buckets.values():
        anchor = _pick_anchor(members)
        out[anchor] = sorted(members)
    return out


def _pick_anchor(refdes_list: list[str]) -> str:
    """Prefer an IC (``U*``) for the cluster id; fall back to first sorted."""
    ics = sorted(r for r in refdes_list if r.startswith("U"))
    if ics:
        return ics[0]
    return sorted(refdes_list)[0]


# ---------------------------------------------------------------------------
# Phase 2 — decoupler assignment
# ---------------------------------------------------------------------------


def assign_decouplers(board: Board) -> dict[str, str]:
    """Map ``cap_refdes → parent_ic_refdes`` for every "decoupling cap".

    A cap counts as a decoupler if:
      * one of its members is on a net listed in the parent IC's
        ``decoupling_pins`` *or* on a power rail, AND
      * the other terminal is on ground.

    When the same cap could attach to multiple ICs, we pick the IC with
    the most explicit ``decoupling_pins`` match for that cap's rail.
    """
    refdes_set = {c.refdes for c in board.components}
    nets_by_member: dict[str, set[str]] = {r: set() for r in refdes_set}
    members_by_net: dict[str, set[str]] = {n.name: set() for n in board.nets}
    for net in board.nets:
        for member in net.members:
            r = member.split(".", 1)[0]
            if r in refdes_set:
                nets_by_member[r].add(net.name)
                members_by_net[net.name].add(r)

    # Identify rails that each IC declares decoupling for.
    ic_rails: dict[str, set[str]] = {}
    for comp in board.components:
        if not comp.decoupling_pins:
            continue
        rails: set[str] = set()
        for net in board.nets:
            for member in net.members:
                refdes, _, pin = member.partition(".")
                if refdes == comp.refdes and pin in comp.decoupling_pins:
                    rails.add(net.name)
        ic_rails[comp.refdes] = rails

    ground_nets = {n.name for n in board.nets if n.net_class == "ground"}
    power_nets = {n.name for n in board.nets if n.net_class == "power"}

    assignments: dict[str, str] = {}
    for comp in board.components:
        if not comp.refdes.startswith("C"):
            continue
        cap_nets = nets_by_member.get(comp.refdes, set())
        has_ground = bool(cap_nets & ground_nets)
        rails_on_cap = cap_nets & power_nets
        if not (has_ground and rails_on_cap):
            continue
        # Find the IC whose declared rails overlap with this cap's rail.
        best_ic: str | None = None
        best_score = 0
        for ic, rails in ic_rails.items():
            score = len(rails & rails_on_cap)
            if score > best_score:
                best_ic = ic
                best_score = score
        # If no IC explicitly declared decoupling, attach to whichever IC
        # is *also* on the same rail (proximity heuristic).
        if best_ic is None:
            for ic in (c.refdes for c in board.components if c.refdes.startswith("U")):
                if rails_on_cap & nets_by_member.get(ic, set()):
                    best_ic = ic
                    break
        if best_ic is not None:
            assignments[comp.refdes] = best_ic
    return assignments


# ---------------------------------------------------------------------------
# Phase 3 — placement
# ---------------------------------------------------------------------------


def layout_schematic(board: Board) -> list[LayoutPlacement]:
    """Top-level. Returns one placement per component, in original order."""
    clusters = cluster_components(board)
    decouplers = assign_decouplers(board)
    partition_by_ref = {c.refdes: c.partition for c in board.components}

    # Fold decoupler-only clusters into the parent IC's cluster. A cap
    # assigned to U1 should sit in U1's halo, not in its own isolated
    # cluster. The parent IC may itself have been merged into a larger
    # cluster (e.g. U1+U2 share an I2S bus), so we look up which cluster
    # currently owns the IC rather than assuming the IC is its own
    # anchor.
    merged: dict[str, list[str]] = {a: list(m) for a, m in clusters.items()}

    def find_cluster(refdes: str) -> str | None:
        for anc, members in merged.items():
            if refdes in members:
                return anc
        return None

    for cap, parent_ic in decouplers.items():
        # Drop the cap from its current home.
        cap_anchor = find_cluster(cap)
        if cap_anchor is not None:
            if len(merged[cap_anchor]) == 1 and merged[cap_anchor][0] == cap:
                merged.pop(cap_anchor, None)
            else:
                merged[cap_anchor].remove(cap)
        # Insert into whichever cluster currently owns the parent IC.
        ic_anchor = find_cluster(parent_ic)
        if ic_anchor is not None:
            if cap not in merged[ic_anchor]:
                merged[ic_anchor].append(cap)
        else:
            # No cluster contains the parent IC — give the cap its own
            # cluster as a safety fallback so we don't lose it.
            merged[cap] = [cap]

    # Order clusters by partition then by anchor name (stable + predictable).
    def cluster_partition(anchor: str) -> str | None:
        return partition_by_ref.get(anchor)

    def partition_rank(p: str | None) -> int:
        try:
            return _PARTITION_ORDER.index(p)
        except ValueError:
            return len(_PARTITION_ORDER)

    ordered_anchors = sorted(
        merged,
        key=lambda a: (partition_rank(cluster_partition(a)), a),
    )

    placements: dict[str, LayoutPlacement] = {}
    x_cursor = _CLUSTER_MARGIN_MM
    for anchor in ordered_anchors:
        members = merged[anchor]
        cluster_center_x = x_cursor + _RADIUS_MM
        cluster_center_y = _PAGE_TOP_MM + _RADIUS_MM
        _place_cluster(
            anchor=anchor,
            members=members,
            decouplers=decouplers,
            center=(cluster_center_x, cluster_center_y),
            out=placements,
        )
        x_cursor += _CLUSTER_GAP_MM + 2 * _RADIUS_MM

    # Preserve the original component order in the returned list.
    return [placements[c.refdes] for c in board.components if c.refdes in placements]


def _place_cluster(
    *,
    anchor: str,
    members: list[str],
    decouplers: dict[str, str],
    center: tuple[float, float],
    out: dict[str, LayoutPlacement],
) -> None:
    cx, cy = center
    cluster_id = anchor

    # 1. Anchor (typically the IC) at the center.
    out[anchor] = LayoutPlacement(
        refdes=anchor,
        x_mm=cx,
        y_mm=cy,
        cluster=cluster_id,
        role="ic" if anchor.startswith("U") else "free",
    )

    # 2. Decouplers ringed around the anchor.
    my_decouplers = sorted([r for r, ic in decouplers.items() if ic == anchor])
    n = max(1, len(my_decouplers))
    for i, refdes in enumerate(my_decouplers):
        theta = (i / n) * 2 * math.pi
        out[refdes] = LayoutPlacement(
            refdes=refdes,
            x_mm=cx + _RADIUS_MM * math.cos(theta),
            y_mm=cy + _RADIUS_MM * math.sin(theta),
            cluster=cluster_id,
            role="decoupler",
        )

    # 3. Other passives & connectors — concentric outer arc. Caps that
    # decouple any IC (even one merged into this cluster from a different
    # partition) still get the "decoupler" role, just placed on the outer
    # arc to keep the anchor's own halo clean.
    others = [
        r for r in members
        if r != anchor and r not in my_decouplers
    ]
    for i, refdes in enumerate(others):
        theta = (i / max(1, len(others))) * 2 * math.pi + math.pi / 8  # offset so not aligned with decouplers
        role = "decoupler" if refdes in decouplers else _role_for_refdes(refdes)
        out[refdes] = LayoutPlacement(
            refdes=refdes,
            x_mm=cx + (_RADIUS_MM + _PASSIVE_STEP_MM) * math.cos(theta),
            y_mm=cy + (_RADIUS_MM + _PASSIVE_STEP_MM) * math.sin(theta),
            cluster=cluster_id,
            role=role,
        )


def _role_for_refdes(refdes: str) -> str:
    if refdes.startswith("J"):
        return "connector"
    if refdes.startswith("U"):
        return "ic"
    return "passive"


# ---------------------------------------------------------------------------
# Helpers exposed for tests
# ---------------------------------------------------------------------------


def cluster_for(board: Board, refdes: str) -> str | None:
    """Convenience for tests: which cluster does ``refdes`` end up in?"""
    clusters = cluster_components(board)
    for anchor, members in clusters.items():
        if refdes in members:
            return anchor
    return None


__all__ = [
    "LayoutPlacement",
    "assign_decouplers",
    "cluster_components",
    "cluster_for",
    "layout_schematic",
]


# Silence the unused-import linter if Net/Component aren't directly used.
_ = (Net, Component)
