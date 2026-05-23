"""CIR-level structural validation.

These checks run *before* anything touches KiCad — they catch issues
that a deterministic check can spot from the typed model alone:
duplicate refdes, dangling net members, missing ground, fab/stackup
mismatches, etc.

ERC/DRC live elsewhere (``ki_mcp_pcb_core.validation``); those wrap
``kicad-cli`` and run against generated artifacts.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel

from ki_mcp_pcb_core.cir.models import Board

Severity = Literal["error", "warning", "info"]


class ValidationIssue(BaseModel):
    severity: Severity
    code: str
    message: str
    where: str | None = None


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = []

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def validate_board(board: Board) -> ValidationReport:
    """Run structural validation against a CIR Board."""
    issues: list[ValidationIssue] = []

    # M0/M1 — structural sanity
    issues.extend(_check_unique_refdes(board))
    issues.extend(_check_net_members_resolve(board))
    issues.extend(_check_ground_present(board))
    issues.extend(_check_stackup_matches_fab(board))

    # M2 — mixed-signal design intent
    issues.extend(_check_decoupling_coverage(board))
    issues.extend(_check_length_match_groups(board))
    issues.extend(_check_partition_isolation(board))

    # M3 — high-speed signal integrity
    issues.extend(_check_diff_pairs(board))
    issues.extend(_check_controlled_impedance(board))
    issues.extend(_check_return_paths(board))

    # M4 — RF / DDR / BGA (co-pilot only)
    issues.extend(_check_ddr_fly_by(board))
    issues.extend(_check_bga_fanout(board))

    return ValidationReport(issues=issues)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_unique_refdes(board: Board) -> list[ValidationIssue]:
    counts = Counter(c.refdes for c in board.components)
    return [
        ValidationIssue(
            severity="error",
            code="CIR001",
            message=f"Duplicate reference designator {ref!r} ({n} components share it).",
            where=ref,
        )
        for ref, n in counts.items()
        if n > 1
    ]


def _check_net_members_resolve(board: Board) -> list[ValidationIssue]:
    refdes_set = {c.refdes for c in board.components}
    issues: list[ValidationIssue] = []
    for net in board.nets:
        for member in net.members:
            refdes, _, pin = member.partition(".")
            if refdes not in refdes_set:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="CIR002",
                        message=f"Net {net.name!r} references unknown component {refdes!r}.",
                        where=f"{net.name}:{member}",
                    )
                )
            elif not pin:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="CIR003",
                        message=f"Net {net.name!r} member {member!r} missing pin number.",
                        where=f"{net.name}:{member}",
                    )
                )
    return issues


def _check_ground_present(board: Board) -> list[ValidationIssue]:
    if not board.components:
        return []  # empty board, nothing to ground
    has_gnd = any(n.net_class == "ground" or n.name.upper() in {"GND", "GROUND", "VSS"} for n in board.nets)
    if not has_gnd:
        return [
            ValidationIssue(
                severity="warning",
                code="CIR010",
                message="No ground net found. Most boards need an explicit GND net.",
            )
        ]
    return []


def _check_stackup_matches_fab(board: Board) -> list[ValidationIssue]:
    copper_layers = [layer for layer in board.stackup.layers if layer.kind == "copper"]
    if len(copper_layers) != board.fab.layer_count:
        return [
            ValidationIssue(
                severity="error",
                code="CIR020",
                message=(
                    f"Stackup has {len(copper_layers)} copper layer(s) but fab target "
                    f"declares {board.fab.layer_count}."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# M2 — design-intent validators
# ---------------------------------------------------------------------------


def _nets_by_member_refdes(board: Board) -> dict[str, list[tuple[str, str]]]:
    """Index: refdes -> list of (pin, net_name) for every net membership."""
    out: dict[str, list[tuple[str, str]]] = {}
    for net in board.nets:
        for member in net.members:
            refdes, _, pin = member.partition(".")
            out.setdefault(refdes, []).append((pin, net.name))
    return out


def _check_decoupling_coverage(board: Board) -> list[ValidationIssue]:
    """CIR030 — every IC with declared decoupling_pins must have at least
    one cap on each supply rail those pins ride. Intent check only; the
    geometric "within N mm" check is M3.
    """
    issues: list[ValidationIssue] = []
    by_refdes = _nets_by_member_refdes(board)

    # Identify caps via MPN/refdes prefix. M3 swaps this for symbol-library inspection.
    caps = {c.refdes for c in board.components if c.refdes.startswith("C")}

    for comp in board.components:
        if not comp.decoupling_pins:
            continue
        # Map supply pins → net names
        supply_nets: set[str] = set()
        for pin in comp.decoupling_pins:
            for member_pin, net_name in by_refdes.get(comp.refdes, []):
                if member_pin == pin:
                    supply_nets.add(net_name)
        for rail in supply_nets:
            # Is any cap connected to BOTH the rail and ground?
            cap_on_rail = {
                ref for ref in caps
                if any(net_name == rail for _, net_name in by_refdes.get(ref, []))
            }
            cap_to_gnd = {
                ref for ref in caps
                if any(net_name.upper() in {"GND", "GROUND", "VSS"}
                       for _, net_name in by_refdes.get(ref, []))
            }
            covering = cap_on_rail & cap_to_gnd
            if not covering:
                issues.append(ValidationIssue(
                    severity="error",
                    code="CIR030",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) declares decoupling needed for "
                        f"net {rail!r} but no capacitor is connected between "
                        f"{rail} and ground."
                    ),
                    where=f"{comp.refdes}:{rail}",
                ))
    return issues


def _check_length_match_groups(board: Board) -> list[ValidationIssue]:
    """CIR040 — length-match group declarations are well-formed.

    Groups must have ≥2 member nets, all referenced nets must exist, and
    the corresponding Constraint (if present) must declare a tolerance.
    Geometric measurement happens post-route at M3.
    """
    issues: list[ValidationIssue] = []
    groups: dict[str, list[str]] = {}
    for net in board.nets:
        if net.length_match_group:
            groups.setdefault(net.length_match_group, []).append(net.name)

    for group_name, members in groups.items():
        if len(members) < 2:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR040",
                message=(
                    f"Length-match group {group_name!r} has only {len(members)} net(s); "
                    "a group must contain at least 2 nets to be matchable."
                ),
                where=group_name,
            ))

    # Check the explicit Constraints reference real groups/nets
    declared_groups = set(groups)
    net_names = {n.name for n in board.nets}
    for c in board.constraints:
        if c.kind == "length_match":
            unknown = [t for t in c.targets if t not in net_names and t not in declared_groups]
            if unknown:
                issues.append(ValidationIssue(
                    severity="error",
                    code="CIR040",
                    message=(
                        f"length_match constraint references unknown nets/groups: "
                        f"{', '.join(unknown)}"
                    ),
                ))
            if c.tolerance_pct is None and c.value_mm is None:
                issues.append(ValidationIssue(
                    severity="warning",
                    code="CIR040",
                    message="length_match constraint has no tolerance_pct or value_mm; "
                            "router can't enforce without one.",
                ))
    return issues


def _check_partition_isolation(board: Board) -> list[ValidationIssue]:
    """CIR050 — signal nets must not span partition boundaries except via
    declared bridge components (Component.is_bridge = True).
    """
    issues: list[ValidationIssue] = []
    partition_by_refdes = {
        c.refdes: c.partition for c in board.components if c.partition is not None
    }
    bridges = {c.refdes for c in board.components if c.is_bridge}

    for net in board.nets:
        if net.net_class in {"ground", "power"}:
            continue  # power/ground are global by definition
        if net.cross_partition_ok:
            continue  # explicitly declared as an intentional crossing
        member_refdes = {m.split(".", 1)[0] for m in net.members}
        # If a bridge component is on this net, the bridge legalizes the
        # crossing — that's its job.
        if member_refdes & bridges:
            continue
        partitions_on_net = {
            partition_by_refdes[r] for r in member_refdes
            if r in partition_by_refdes
        }
        if len(partitions_on_net) > 1:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR050",
                message=(
                    f"Net {net.name!r} crosses partitions "
                    f"{sorted(partitions_on_net)} without a declared bridge. "
                    "Either add a bridge component (ferrite bead, opto, "
                    "capacitor) and mark it with is_bridge=true, OR set "
                    "cross_partition_ok=true on the net to confirm the "
                    "crossing is intentional."
                ),
                where=net.name,
            ))
    return issues


# ---------------------------------------------------------------------------
# M3 — high-speed signal integrity
# ---------------------------------------------------------------------------


def _check_diff_pairs(board: Board) -> list[ValidationIssue]:
    """CIR060 — every declared diff pair half:

      * names a real other net,
      * the other net points back at us,
      * both halves share a length_match_group.
    """
    issues: list[ValidationIssue] = []
    by_name = {n.name: n for n in board.nets}
    for net in board.nets:
        partner_name = net.diff_pair_with
        if partner_name is None:
            continue
        partner = by_name.get(partner_name)
        if partner is None:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR060",
                message=(
                    f"Net {net.name!r} declares diff_pair_with={partner_name!r} "
                    "but no such net exists."
                ),
                where=net.name,
            ))
            continue
        if partner.diff_pair_with != net.name:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR060",
                message=(
                    f"Diff pair declaration not bidirectional: "
                    f"{net.name!r} points to {partner_name!r} but "
                    f"{partner_name!r} points to {partner.diff_pair_with!r}."
                ),
                where=net.name,
            ))
            continue
        if not (net.length_match_group and net.length_match_group == partner.length_match_group):
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR060",
                message=(
                    f"Diff pair {net.name!r}/{partner_name!r} should share a "
                    "length_match_group so the router enforces matched length. "
                    f"Got {net.length_match_group!r} / {partner.length_match_group!r}."
                ),
                where=net.name,
            ))
    return issues


def _check_controlled_impedance(board: Board) -> list[ValidationIssue]:
    """CIR070 — every net with ``target_impedance_ohm`` set has a stackup
    that can actually achieve that target with the default trace geometry.

    Tolerance: ±20%. Tighter targets need explicit trace width overrides
    (M4 work — for now we report the achievable value and let the user
    choose to adjust trace width or stackup).
    """
    from ki_mcp_pcb_core.signal_integrity import (
        differential_microstrip_impedance,
        geometry_for_net,
        grounded_cpwg_impedance,
        microstrip_impedance,
    )

    issues: list[ValidationIssue] = []
    for net in board.nets:
        target = net.target_impedance_ohm
        if target is None:
            continue
        geo = geometry_for_net(board, net)
        if geo is None:
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR070",
                message=(
                    f"Net {net.name!r} declares target_impedance_ohm={target} "
                    "but the stackup has no dielectric layers with εr set, so "
                    "the achievable impedance can't be computed."
                ),
                where=net.name,
            ))
            continue
        try:
            if net.cpwg_gap_mm is not None:
                achieved = grounded_cpwg_impedance(geo)
            elif net.diff_pair_with:
                achieved = differential_microstrip_impedance(geo)
            else:
                achieved = microstrip_impedance(geo)
        except ValueError as exc:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR070",
                message=f"Net {net.name!r}: impedance math failed: {exc}",
                where=net.name,
            ))
            continue
        deviation = abs(achieved - target) / target
        if deviation > 0.20:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR070",
                message=(
                    f"Net {net.name!r} target {target:.0f} Ω is "
                    f"{deviation * 100:.0f}% off the achievable "
                    f"{achieved:.1f} Ω with the current stackup + default "
                    "trace geometry. Adjust trace width, dielectric "
                    "thickness, or pair spacing."
                ),
                where=net.name,
            ))
        elif deviation > 0.10:
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR070",
                message=(
                    f"Net {net.name!r} target {target:.0f} Ω vs achievable "
                    f"{achieved:.1f} Ω: {deviation * 100:.0f}% off. "
                    "Within the 20% gate but trace tuning recommended."
                ),
                where=net.name,
            ))
    return issues


def _check_return_paths(board: Board) -> list[ValidationIssue]:
    """CIR090 — high-speed nets declare a reference plane that exists in
    the stackup as a copper layer.

    Geometric "plane is contiguous under the trace" detection is post-route
    work — the CIR-level check confirms the intent is well-formed.
    """
    issues: list[ValidationIssue] = []
    copper_layer_names = {
        layer.name for layer in board.stackup.layers if layer.kind == "copper"
    }
    hs_classes = {"high_speed", "differential", "rf"}
    for net in board.nets:
        # Skip non-HS nets that don't bother to declare a plane. If a
        # non-HS net DID declare one, we still validate it.
        if net.net_class not in hs_classes and net.reference_plane is None:
            continue
        if net.reference_plane is None:
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR090",
                message=(
                    f"High-speed net {net.name!r} (class={net.net_class}) has no "
                    "reference_plane declared. Set the stackup layer this net's "
                    "return current rides (e.g. 'In1.Cu') so reviewers can verify "
                    "the plane is contiguous beneath the trace."
                ),
                where=net.name,
            ))
            continue
        if net.reference_plane not in copper_layer_names:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR090",
                message=(
                    f"Net {net.name!r} references plane {net.reference_plane!r} "
                    f"which isn't a copper layer in the stackup. "
                    f"Known copper: {sorted(copper_layer_names)}."
                ),
                where=net.name,
            ))
    return issues


# ---------------------------------------------------------------------------
# M4 — RF / DDR / BGA fanout (co-pilot only)
# ---------------------------------------------------------------------------


def _check_ddr_fly_by(board: Board) -> list[ValidationIssue]:
    """CIR100 — DDR-style fly-by topology nets are well-formed.

    A fly-by net declares ``topology="fly_by"`` and a ``fly_by_order`` list
    of refdes. The first entry is the controller, the last is the
    terminator (or terminating resistor), middle entries are RAM devices.
    """
    issues: list[ValidationIssue] = []
    refdes_set = {c.refdes for c in board.components}

    for net in board.nets:
        if net.topology != "fly_by":
            continue
        if len(net.fly_by_order) < 3:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR100",
                message=(
                    f"Net {net.name!r} declares topology='fly_by' but has only "
                    f"{len(net.fly_by_order)} entries in fly_by_order. A valid "
                    "fly-by needs at least controller → ram → terminator (3+)."
                ),
                where=net.name,
            ))
            continue
        unknown = [r for r in net.fly_by_order if r not in refdes_set]
        if unknown:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR100",
                message=(
                    f"Fly-by net {net.name!r} references unknown components: "
                    f"{', '.join(unknown)}."
                ),
                where=net.name,
            ))
            continue
        # Members must include every refdes in fly_by_order
        member_refdes = {m.split(".", 1)[0] for m in net.members}
        missing_from_members = [
            r for r in net.fly_by_order if r not in member_refdes
        ]
        if missing_from_members:
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR100",
                message=(
                    f"Fly-by net {net.name!r} order lists "
                    f"{missing_from_members} but they don't appear in net.members."
                ),
                where=net.name,
            ))
        # If the board hasn't been signed off for DDR, escalate to warning.
        if not board.signoff.ddr_reviewed:
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR100",
                message=(
                    f"Fly-by net {net.name!r} requires human EE sign-off. "
                    "Set board.signoff.ddr_reviewed=true with reviewer + date "
                    "after a human checks the topology, lengths, and termination."
                ),
                where=net.name,
            ))
    return issues


def _check_bga_fanout(board: Board) -> list[ValidationIssue]:
    """CIR110 — declared BGA pitches are achievable on the fab target.

    Looks up the pitch in ``libs/bga_fanout.yaml`` and checks the recommended
    via/trace/clearance against ``board.fab`` minimums.
    """
    from ki_mcp_pcb_core.cir._bga_fanout import bga_fanout_for_pitch  # local import

    issues: list[ValidationIssue] = []
    fab = board.fab
    for comp in board.components:
        if comp.bga_pitch_mm is None:
            continue
        template = bga_fanout_for_pitch(comp.bga_pitch_mm)
        if template is None:
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR110",
                message=(
                    f"{comp.refdes}: BGA pitch {comp.bga_pitch_mm} mm has no "
                    "fanout template in libs/bga_fanout.yaml. Add an entry or "
                    "verify escape routing manually."
                ),
                where=comp.refdes,
            ))
            continue
        problems: list[str] = []
        if template.escape_trace_width_mm < fab.min_trace_mm:
            problems.append(
                f"escape trace {template.escape_trace_width_mm} mm < "
                f"fab min_trace {fab.min_trace_mm} mm"
            )
        if template.escape_clearance_mm < fab.min_space_mm:
            problems.append(
                f"escape clearance {template.escape_clearance_mm} mm < "
                f"fab min_space {fab.min_space_mm} mm"
            )
        if template.via_drill_mm < fab.min_drill_mm:
            problems.append(
                f"via drill {template.via_drill_mm} mm < fab min_drill "
                f"{fab.min_drill_mm} mm"
            )
        if template.requires_hdi:
            problems.append("template requires HDI / micro-vias — not standard JLC")
        if problems:
            issues.append(ValidationIssue(
                severity="error",
                code="CIR110",
                message=(
                    f"{comp.refdes}: BGA pitch {comp.bga_pitch_mm} mm escape "
                    f"requires capabilities the {fab.name} target doesn't have: "
                    + "; ".join(problems)
                ),
                where=comp.refdes,
            ))
        if not board.signoff.bga_fanout_reviewed:
            issues.append(ValidationIssue(
                severity="warning",
                code="CIR110",
                message=(
                    f"{comp.refdes}: BGA fanout requires human review. Set "
                    "board.signoff.bga_fanout_reviewed=true after escape "
                    "routing is verified."
                ),
                where=comp.refdes,
            ))
    return issues
