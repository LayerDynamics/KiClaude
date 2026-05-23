"""Diff two CIR ``Board`` objects.

Used by ``kimp diff`` and the MCP ``pcb_diff`` tool. Comparing two
KiCad projects works the same way: read each one with
``KiCadBackend.read_project`` first, then diff the Boards.

Output is a structured ``BoardDiff`` so callers (CLI, MCP) can render
however they like — Rich tables in the CLI, JSON over MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ki_mcp_pcb_core.cir.models import Board, Component, Net


@dataclass(frozen=True)
class ComponentChange:
    refdes: str
    field: str
    left: str | None
    right: str | None


@dataclass(frozen=True)
class NetChange:
    name: str
    field: str
    left: str
    right: str


@dataclass(frozen=True)
class BoardDiff:
    name_changed: tuple[str, str] | None = None
    components_added: list[str] = field(default_factory=list)
    components_removed: list[str] = field(default_factory=list)
    component_changes: list[ComponentChange] = field(default_factory=list)
    nets_added: list[str] = field(default_factory=list)
    nets_removed: list[str] = field(default_factory=list)
    net_changes: list[NetChange] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not (
            self.name_changed
            or self.components_added
            or self.components_removed
            or self.component_changes
            or self.nets_added
            or self.nets_removed
            or self.net_changes
        )

    def summary(self) -> str:
        bits = []
        if self.name_changed:
            bits.append(f"name {self.name_changed[0]!r} → {self.name_changed[1]!r}")
        if self.components_added:
            bits.append(f"+{len(self.components_added)} component(s)")
        if self.components_removed:
            bits.append(f"-{len(self.components_removed)} component(s)")
        if self.component_changes:
            bits.append(f"{len(self.component_changes)} component field change(s)")
        if self.nets_added:
            bits.append(f"+{len(self.nets_added)} net(s)")
        if self.nets_removed:
            bits.append(f"-{len(self.nets_removed)} net(s)")
        if self.net_changes:
            bits.append(f"{len(self.net_changes)} net change(s)")
        if not bits:
            return "identical"
        return ", ".join(bits)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


_COMP_FIELDS = ("mpn", "value", "footprint", "symbol", "partition")
_NET_FIELDS = ("net_class", "power_rail", "partition", "target_impedance_ohm",
               "length_match_group", "diff_pair_with", "reference_plane",
               "topology")


def diff_boards(left: Board, right: Board) -> BoardDiff:
    """Structural diff of two boards. Pure function — no I/O."""
    name_changed = (left.name, right.name) if left.name != right.name else None

    left_comps = {c.refdes: c for c in left.components}
    right_comps = {c.refdes: c for c in right.components}
    added = sorted(set(right_comps) - set(left_comps))
    removed = sorted(set(left_comps) - set(right_comps))

    comp_changes: list[ComponentChange] = []
    for ref in sorted(set(left_comps) & set(right_comps)):
        comp_changes.extend(_compare_component(left_comps[ref], right_comps[ref]))

    left_nets = {n.name: n for n in left.nets}
    right_nets = {n.name: n for n in right.nets}
    nets_added = sorted(set(right_nets) - set(left_nets))
    nets_removed = sorted(set(left_nets) - set(right_nets))

    net_changes: list[NetChange] = []
    for name in sorted(set(left_nets) & set(right_nets)):
        net_changes.extend(_compare_net(left_nets[name], right_nets[name]))

    return BoardDiff(
        name_changed=name_changed,
        components_added=added,
        components_removed=removed,
        component_changes=comp_changes,
        nets_added=nets_added,
        nets_removed=nets_removed,
        net_changes=net_changes,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compare_component(left: Component, right: Component) -> list[ComponentChange]:
    out: list[ComponentChange] = []
    for field_name in _COMP_FIELDS:
        lv = getattr(left, field_name, None)
        rv = getattr(right, field_name, None)
        if lv != rv:
            out.append(ComponentChange(
                refdes=left.refdes,
                field=field_name,
                left=str(lv) if lv is not None else None,
                right=str(rv) if rv is not None else None,
            ))
    return out


def _compare_net(left: Net, right: Net) -> list[NetChange]:
    out: list[NetChange] = []
    for field_name in _NET_FIELDS:
        lv = getattr(left, field_name, None)
        rv = getattr(right, field_name, None)
        if lv != rv:
            out.append(NetChange(name=left.name, field=field_name,
                                  left=str(lv), right=str(rv)))
    if set(left.members) != set(right.members):
        out.append(NetChange(
            name=left.name,
            field="members",
            left=",".join(sorted(left.members)),
            right=",".join(sorted(right.members)),
        ))
    return out
