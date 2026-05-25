"""`kc_validate` — KCIR structural + M5 co-pilot validators.

Runs KCIR-only sanity checks that don't require a running kicad-cli:

- KC001..KC011 — structural integrity (refdes, footprints, nets,
  hierarchy) — M1-P-04. NUMBERING NOTE: these 11 are kiclaude's own
  structural set and predate the SPEC §7.3 table; their codes do NOT
  line up 1:1 with §7.3's semantics (e.g. §7.3 KC001 "unique refdes"
  is enforced here at KC006). The §7.3 *design-intent* codes below are
  authoritative for their numbers; reconciling the structural 1-11
  with §7.3 is a tracked doc task (Todo §2), deferred to avoid
  renumbering an established, tested surface.
- KC020 — every IC has a bypass cap on each power rail it uses.
- KC021 — every power-rail net has a source (PWR_FLAG / active driver).
- KC030 — length-match groups have >= 2 members.
- KC031 — diff pairs declared bidirectionally + share a length group.
- KC040 — controlled-impedance targets achievable on the stackup
  (IPC-2141A estimate; >10% warn, >20% error — the authoritative
  Hammerstad check is `kc_impedance_check`).
- KC050 — analog/digital ground partitions tied by a single bridge.
- KC060 — DDR fly-by topology reaches >= 3 nodes and is signed off
  (warns until `pcb.signoff.ddr_reviewed`).
- KC070 — BGA fanout feasible on the fab DFM rules (warns until
  `pcb.signoff.bga_fanout_reviewed`).

Real ERC (electrical-rule-check) lives in `tools/erc.py` and shells
out to `kicad-cli sch erc`.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get


@tool(
    "kc_validate",
    "Run the KCIR-level sanity validators on an opened project: "
    "KC001..KC011 structural checks; the design-intent validators "
    "KC020 (decoupling), KC021 (power-rail source), KC030 (length-match), "
    "KC031 (diff pairs), KC040 (impedance), KC050 (partitions); and the "
    "M5 co-pilot validators KC060 (DDR fly-by) + KC070 (BGA fanout). "
    "Returns findings with `code`, `severity`, `message`, and optional "
    "`target_uuid`. Read-only.",
    {"project_id": str},
)
async def kc_validate(args: dict[str, Any]) -> dict[str, Any]:
    project_id = args.get("project_id", "")
    if not project_id:
        return error_envelope("`project_id` is required")
    try:
        result = await kiserver_get(f"/project/{project_id}")
    except Exception as e:
        return error_envelope(
            f"kiserver GET /project/{project_id} failed: {e}",
            project_id=project_id,
        )
    project = result.get("project", {})
    findings = _run_validators(project)
    return envelope(
        {
            "ok": True,
            "project_id": project_id,
            "findings": findings,
            "summary": {
                "error": sum(1 for f in findings if f["severity"] == "error"),
                "warning": sum(1 for f in findings if f["severity"] == "warning"),
                "info": sum(1 for f in findings if f["severity"] == "info"),
            },
        }
    )


def _run_validators(project: dict[str, Any]) -> list[dict[str, Any]]:
    """The KC001..KC011 validator pass. Pure function over the KCIR
    dict so the schematic editor (M1-T-01) can preview findings
    without going through HTTP."""
    schematic = project.get("schematic", {})
    pcb = project.get("pcb", {})
    symbols: list[dict[str, Any]] = schematic.get("symbols", []) or []
    findings: list[dict[str, Any]] = []

    # KC001: every symbol has a non-empty refdes (post-annotation).
    for s in symbols:
        if s.get("is_power_symbol") or s.get("is_power_flag"):
            continue  # Power symbols carry `#PWR<N>` / `#FLG<N>` after annotate.
        if not s.get("refdes"):
            findings.append(
                {
                    "code": "KC001",
                    "severity": "error",
                    "message": f"Symbol {s.get('uuid', '<no-uuid>')} has no refdes (run annotate).",
                    "target_uuid": s.get("uuid"),
                }
            )

    # KC002: every footprint has a lib_id.
    for fp in pcb.get("footprints", []) or []:
        if not fp.get("lib_id"):
            findings.append(
                {
                    "code": "KC002",
                    "severity": "error",
                    "message": f"Footprint {fp.get('uuid', '<no-uuid>')} has no lib_id.",
                    "target_uuid": fp.get("uuid"),
                }
            )

    # KC003: hierarchical labels point to a matching sheet pin (taken
    # from KCIR Schematic.sheets[*].pins). Orphan hierarchical labels
    # are flagged.
    pin_index = {
        (sheet.get("uuid", ""), pin.get("name", ""))
        for sheet in schematic.get("sheets", []) or []
        for pin in sheet.get("pins", []) or []
    }
    for label in schematic.get("labels", []) or []:
        if label.get("kind") != "hierarchical":
            continue
        key = (label.get("sheet_uuid", ""), label.get("text", ""))
        if key not in pin_index:
            findings.append(
                {
                    "code": "KC003",
                    "severity": "warning",
                    "message": (
                        f"Hierarchical label '{label.get('text', '')}' has no "
                        "matching sheet pin on its parent's (sheet …) block."
                    ),
                    "target_uuid": label.get("uuid"),
                }
            )

    # KC004: no two sub-sheets under the same parent define a pin with
    # the same name (the resolver records this; we mirror it here so
    # `kc_validate` is callable without first running the resolver).
    parent_pins: dict[tuple[str, str], list[str]] = {}
    for sheet in schematic.get("sheets", []) or []:
        parent_uuid = sheet.get("parent")
        if not parent_uuid:
            continue
        for pin in sheet.get("pins", []) or []:
            key2 = (parent_uuid, pin.get("name", ""))
            parent_pins.setdefault(key2, []).append(sheet.get("uuid", ""))
    for (parent_uuid, pin_name), claimers in parent_pins.items():
        if len(claimers) > 1:
            findings.append(
                {
                    "code": "KC004",
                    "severity": "warning",
                    "message": (
                        f"Pin '{pin_name}' is claimed by {len(claimers)} sub-sheets "
                        f"under parent {parent_uuid}."
                    ),
                    "target_uuid": parent_uuid,
                }
            )

    # KC005: every component symbol carries a Footprint property.
    for s in symbols:
        if s.get("is_power_symbol"):
            continue
        if not s.get("footprint"):
            findings.append(
                {
                    "code": "KC005",
                    "severity": "warning",
                    "message": (
                        f"Symbol {s.get('refdes') or s.get('uuid')} has no "
                        "Footprint property — BOM/PCB net-listing will fail."
                    ),
                    "target_uuid": s.get("uuid"),
                }
            )

    # KC006: no duplicate refdes (after annotate).
    refdes_counts = Counter(s.get("refdes") for s in symbols if s.get("refdes"))
    for refdes, count in refdes_counts.items():
        if count > 1:
            findings.append(
                {
                    "code": "KC006",
                    "severity": "error",
                    "message": f"Duplicate refdes {refdes!r} ({count} occurrences).",
                    "target_uuid": None,
                }
            )

    # KC007: every nets entry has a non-empty name (the implicit "no
    # net" 0 is never represented in `kcir::nets`).
    for net in pcb.get("nets", []) or []:
        if not net.get("name"):
            findings.append(
                {
                    "code": "KC007",
                    "severity": "warning",
                    "message": "PCB net entry with empty name.",
                    "target_uuid": None,
                }
            )

    # KC008: every power-net symbol is flagged.
    for s in symbols:
        lib_id = s.get("lib_id", "") or ""
        if lib_id.startswith("power:") and not s.get("is_power_symbol"):
            findings.append(
                {
                    "code": "KC008",
                    "severity": "warning",
                    "message": (
                        f"Symbol {s.get('refdes') or s.get('uuid')} has lib_id "
                        f"{lib_id} but is_power_symbol is false."
                    ),
                    "target_uuid": s.get("uuid"),
                }
            )

    # KC009: every sheet has either parent=None or a parent uuid that
    # actually exists in the project.
    sheet_uuids = {s.get("uuid") for s in schematic.get("sheets", []) or []}
    for sheet in schematic.get("sheets", []) or []:
        parent_uuid = sheet.get("parent")
        if parent_uuid is not None and parent_uuid not in sheet_uuids:
            findings.append(
                {
                    "code": "KC009",
                    "severity": "error",
                    "message": (
                        f"Sheet '{sheet.get('name')}' has parent={parent_uuid} "
                        "but no such sheet exists in the project."
                    ),
                    "target_uuid": sheet.get("uuid"),
                }
            )

    # KC010: every non-power component symbol has a non-empty value.
    for s in symbols:
        if s.get("is_power_symbol") or s.get("is_power_flag"):
            continue
        if not (s.get("value") or "").strip():
            findings.append(
                {
                    "code": "KC010",
                    "severity": "info",
                    "message": (f"Symbol {s.get('refdes') or s.get('uuid')} has an empty Value."),
                    "target_uuid": s.get("uuid"),
                }
            )

    # KC011: every footprint instance has a matching schematic symbol
    # by refdes — a basic netlist-consistency probe.
    symbol_refdes = {s.get("refdes") for s in symbols if s.get("refdes")}
    for fp in pcb.get("footprints", []) or []:
        ref = fp.get("refdes")
        if ref and ref not in symbol_refdes:
            findings.append(
                {
                    "code": "KC011",
                    "severity": "warning",
                    "message": (
                        f"Footprint {ref} has no matching schematic symbol with "
                        "the same refdes — PCB and schematic are out of sync."
                    ),
                    "target_uuid": fp.get("uuid"),
                }
            )

    # ---- M5 co-pilot validators (SPEC §7.3) -------------------------
    # These read the M5 `pcb.signoff` gate (KCIR 0.5). Each surfaces a
    # warning until the human ticks the matching sign-off flag; the LLM
    # cannot set those flags (agent PreToolUse gate).
    signoff = pcb.get("signoff", {}) or {}

    # KC060: a DDR fly-by net must reach >= 3 nodes (controller + >= 2
    # loads) and be human-reviewed. Warning until `signoff.ddr_reviewed`.
    ddr_reviewed = bool(signoff.get("ddr_reviewed", False))
    for net in pcb.get("nets", []) or []:
        if net.get("topology") != "fly_by":
            continue
        nodes = len(net.get("members", []) or [])
        if nodes < 3:
            findings.append(
                {
                    "code": "KC060",
                    "severity": "error",
                    "message": (
                        f"Fly-by net '{net.get('name')}' has {nodes} node(s); a DDR "
                        "fly-by topology needs >= 3 (controller + >= 2 loads)."
                    ),
                    "target_uuid": None,
                }
            )
        elif not ddr_reviewed:
            findings.append(
                {
                    "code": "KC060",
                    "severity": "warning",
                    "message": (
                        f"DDR fly-by net '{net.get('name')}' ({nodes} nodes) has not "
                        "been human-reviewed — set pcb.signoff.ddr_reviewed after a "
                        "topology + termination review."
                    ),
                    "target_uuid": None,
                }
            )

    # KC070: BGA fanout feasibility on the declared fab DFM rules. A
    # single-layer dog-bone escape needs roughly
    #   ball_pitch >= via_diameter + clearance + trace_width.
    # Infeasible + unreviewed is an error (needs HDI/microvia or signoff);
    # feasible + unreviewed is a warning; reviewed clears (info).
    bga_reviewed = bool(signoff.get("bga_fanout_reviewed", False))
    rules = project.get("design_rules", {}) or {}
    via_dia = float(rules.get("via_diameter_mm", 0.0) or 0.0)
    clearance = float(rules.get("clearance_mm", 0.0) or 0.0)
    trace = float(rules.get("trace_width_mm", 0.0) or 0.0)
    need_pitch = via_dia + clearance + trace
    for fp in pcb.get("footprints", []) or []:
        pads = fp.get("pads", []) or []
        if not _is_bga_footprint(fp.get("lib_id", "") or "", pads):
            continue
        pitch = _min_pad_pitch(pads)
        ref = fp.get("refdes") or fp.get("uuid")
        if pitch is None:
            continue
        if need_pitch > 0.0 and pitch + 1e-9 < need_pitch:
            findings.append(
                {
                    "code": "KC070",
                    "severity": "info" if bga_reviewed else "error",
                    "message": (
                        f"BGA {ref} ball pitch {pitch:.3f} mm is below the "
                        f"{need_pitch:.3f} mm a single-layer dog-bone escape needs "
                        f"(via {via_dia:.3f} + clearance {clearance:.3f} + trace "
                        f"{trace:.3f}); requires HDI/microvia fanout"
                        + (
                            " — accepted via pcb.signoff.bga_fanout_reviewed."
                            if bga_reviewed
                            else "."
                        )
                    ),
                    "target_uuid": fp.get("uuid"),
                }
            )
        elif not bga_reviewed:
            findings.append(
                {
                    "code": "KC070",
                    "severity": "warning",
                    "message": (
                        f"BGA {ref} fanout looks feasible at {pitch:.3f} mm pitch but "
                        "has not been human-reviewed — set "
                        "pcb.signoff.bga_fanout_reviewed after a fanout review."
                    ),
                    "target_uuid": fp.get("uuid"),
                }
            )

    # ---- M3 design-intent validators (SPEC §7.3) -------------------
    nets = pcb.get("nets", []) or []
    footprints = pcb.get("footprints", []) or []

    # Index net membership from footprint pad nets: net -> [(refdes, class)],
    # and refdes -> set(nets).
    net_members: dict[str, list[tuple[str, str]]] = {}
    fp_nets: dict[str, set[str]] = {}
    for fp in footprints:
        ref = fp.get("refdes") or ""
        cls = _refdes_class(ref)
        nset: set[str] = set()
        for pad in fp.get("pads", []) or []:
            n = pad.get("net") or ""
            if n:
                nset.add(n)
                net_members.setdefault(n, []).append((ref, cls))
        if ref:
            fp_nets[ref] = nset

    # KC020 — every IC must have a bypass cap on each power rail it uses.
    for fp in footprints:
        ref = fp.get("refdes") or ""
        if _refdes_class(ref) != "ic":
            continue
        power_nets = {n for n in fp_nets.get(ref, set()) if _is_power_net(n)}
        unbypassed = [
            pn
            for pn in sorted(power_nets)
            if not any(c == "cap" for (_, c) in net_members.get(pn, []))
        ]
        if power_nets and unbypassed:
            findings.append(
                {
                    "code": "KC020",
                    "severity": "error",
                    "message": (
                        f"IC {ref} has no bypass capacitor on power net(s) "
                        f"{', '.join(unbypassed)} — add decoupling to ground."
                    ),
                    "target_uuid": fp.get("uuid"),
                }
            )

    # KC021 — every power-rail net needs a source (PWR_FLAG or active driver).
    flagged = _power_flag_nets(schematic)
    for net in nets:
        name = net.get("name") or ""
        if not _is_power_net(name):
            continue
        members = net_members.get(name, [])
        if not members:
            continue  # unpopulated net — nothing to assess yet
        has_active = any(c in ("ic", "reg", "active") for (_, c) in members)
        if has_active or name in flagged:
            continue
        findings.append(
            {
                "code": "KC021",
                "severity": "error",
                "message": (
                    f"Power rail '{name}' has no source — only passives connect to "
                    "it and there's no regulator/active driver or PWR_FLAG. ERC will "
                    "flag it as undriven."
                ),
                "target_uuid": None,
            }
        )

    # KC030 — length-match groups need >= 2 members.
    for grp in pcb.get("length_groups", []) or []:
        gnets = grp.get("nets", []) or []
        if len(gnets) < 2:
            findings.append(
                {
                    "code": "KC030",
                    "severity": "error",
                    "message": (
                        f"Length-match group '{grp.get('name')}' has {len(gnets)} "
                        "member(s); a group needs >= 2 nets to match against."
                    ),
                    "target_uuid": None,
                }
            )

    # KC031 — diff pairs declared bidirectionally + sharing a length group.
    net_by_name = {n.get("name"): n for n in nets}
    for dp in pcb.get("diff_pairs", []) or []:
        pos = dp.get("net_positive") or ""
        neg = dp.get("net_negative") or ""
        dpname = dp.get("name") or "<unnamed>"
        if not pos or not neg:
            findings.append(
                {
                    "code": "KC031",
                    "severity": "error",
                    "message": f"Diff pair '{dpname}' is missing a leg (+='{pos}', -='{neg}').",
                    "target_uuid": None,
                }
            )
            continue
        unknown = [n for n in (pos, neg) if n not in net_by_name]
        if unknown:
            findings.append(
                {
                    "code": "KC031",
                    "severity": "error",
                    "message": (
                        f"Diff pair '{dpname}' references unknown net(s): "
                        f"{', '.join(unknown)}."
                    ),
                    "target_uuid": None,
                }
            )
            continue
        pos_ref = _netref(net_by_name[pos].get("diff_pair"))
        neg_ref = _netref(net_by_name[neg].get("diff_pair"))
        if pos_ref != neg or neg_ref != pos:
            findings.append(
                {
                    "code": "KC031",
                    "severity": "warning",
                    "message": (
                        f"Diff pair '{dpname}' is not declared bidirectionally — the "
                        f"Net.diff_pair back-refs on '{pos}'/'{neg}' don't point at "
                        "each other."
                    ),
                    "target_uuid": None,
                }
            )
        if not (dp.get("length_group") or ""):
            findings.append(
                {
                    "code": "KC031",
                    "severity": "warning",
                    "message": (
                        f"Diff pair '{dpname}' shares no length-match group; inter-leg "
                        "skew is unconstrained."
                    ),
                    "target_uuid": None,
                }
            )

    # KC040 — controlled-impedance achievable on the declared stackup.
    # Warning >10% off target, error >20% (SPEC §7.3). IPC-2141A microstrip
    # estimate here; the authoritative Hammerstad solver is kc_impedance_check.
    er, h = _outer_microstrip_geometry(project.get("stackup", {}) or {})
    for net in nets:
        target = net.get("target_impedance_ohm")
        if not target:
            continue
        width = _net_class_width(project, net.get("class"))
        if er is None or h is None or not width:
            findings.append(
                {
                    "code": "KC040",
                    "severity": "warning",
                    "message": (
                        f"Net '{net.get('name')}' targets {target} ohm but the data to "
                        "evaluate it is missing (no outer-dielectric Er/height or no "
                        "net-class trace width)."
                    ),
                    "target_uuid": None,
                }
            )
            continue
        z0 = _microstrip_z0(width, h, er)
        off = abs(z0 - float(target)) / float(target)
        if off > 0.20:
            findings.append(
                {
                    "code": "KC040",
                    "severity": "error",
                    "message": (
                        f"Net '{net.get('name')}' target {target} ohm is not achievable: "
                        f"{width:.3f} mm on this stackup gives ~{z0:.1f} ohm "
                        f"({off * 100:.0f}% off)."
                    ),
                    "target_uuid": None,
                }
            )
        elif off > 0.10:
            findings.append(
                {
                    "code": "KC040",
                    "severity": "warning",
                    "message": (
                        f"Net '{net.get('name')}' target {target} ohm is {off * 100:.0f}% "
                        f"off (~{z0:.1f} ohm at {width:.3f} mm) — tune width or stackup."
                    ),
                    "target_uuid": None,
                }
            )

    # KC050 — analog/digital partition isolation: exactly one ground bridge.
    for agnd, dgnd in _analog_digital_ground_pairs(nets):
        bridges = sorted(r for r, ns in fp_nets.items() if {agnd, dgnd} <= ns)
        if len(bridges) > 1:
            findings.append(
                {
                    "code": "KC050",
                    "severity": "error",
                    "message": (
                        f"Partition violation: {agnd} and {dgnd} are tied by "
                        f"{len(bridges)} components ({', '.join(bridges)}); a split-"
                        "ground design needs exactly one bridge (single-point tie)."
                    ),
                    "target_uuid": None,
                }
            )

    return findings


def _refdes_class(refdes: str) -> str:
    """Coarse part class from the refdes prefix letters."""
    prefix = "".join(ch for ch in refdes if ch.isalpha()).upper()
    if prefix in {"U", "IC"}:
        return "ic"
    if prefix in {"VR", "REG"}:
        return "reg"
    if prefix in {"Q"}:
        return "active"
    if prefix in {"C"}:
        return "cap"
    if prefix in {"R", "L", "FB"}:
        return "passive"
    return "other"


_POWER_TOKENS = ("VCC", "VDD", "VBUS", "VBAT", "VAA", "VREF", "VSYS", "VIN", "VCORE", "VPP")


def _is_power_net(name: str) -> bool:
    """Heuristic: is this net a power rail (not a ground)?"""
    if not name:
        return False
    up = name.upper()
    if "GND" in up or up.startswith("VSS") or up.startswith("AGND") or up.startswith("DGND"):
        return False
    return name.startswith("+") or any(tok in up for tok in _POWER_TOKENS)


def _power_flag_nets(schematic: dict[str, Any] | None) -> set[str]:
    if not schematic:
        return set()
    """Net names that carry a power symbol / PWR_FLAG in the schematic —
    treated as having a declared source for KC021."""
    out: set[str] = set()
    for s in schematic.get("symbols", []) or []:
        if s.get("is_power_flag") or s.get("is_power_symbol"):
            val = (s.get("value") or "").strip()
            if val:
                out.add(val)
    return out


def _netref(raw: Any) -> str:
    """A KCIR `NetRef` serialises as a bare string (the net name); be
    tolerant of `null` or a wrapped form."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for v in raw.values():
            if isinstance(v, str):
                return v
    return ""


def _outer_microstrip_geometry(stackup: dict[str, Any]) -> tuple[float | None, float | None]:
    """Er + height (mm) of the first dielectric below the top copper — the
    geometry an outer-layer microstrip rides on. `(None, None)` if absent."""
    layers = stackup.get("layers", []) or []
    seen_copper = False
    for layer in layers:
        kind = (layer.get("kind") or "").lower()
        if kind == "copper" and not seen_copper:
            seen_copper = True
            continue
        if seen_copper and kind in {"dielectric", "core", "prepreg"}:
            er = layer.get("dielectric_constant")
            h = layer.get("thickness_mm")
            if er and h:
                return float(er), float(h)
            return None, None
    return None, None


def _net_class_width(project: dict[str, Any], class_ref: Any) -> float | None:
    """Trace width (mm) of the net's class, from project or PCB net classes."""
    name = _netref(class_ref) or "Default"
    pools = (project.get("net_classes", []) or []) + (
        (project.get("pcb", {}) or {}).get("net_classes", []) or []
    )
    for nc in pools:
        if nc.get("name") == name:
            w = nc.get("track_width_mm") or nc.get("trace_width_mm") or nc.get("track_width")
            return float(w) if w else None
    return None


def _microstrip_z0(w_mm: float, h_mm: float, er: float, t_mm: float = 0.035) -> float:
    """IPC-2141A surface-microstrip characteristic impedance (ohms).

    A standards closed form — an estimate for the KC040 achievability
    gate; the authoritative solver is the Rust Hammerstad-Jensen in
    `crates/cad/src/impedance.rs` (exposed as `kc_impedance_check`).
    """
    if w_mm <= 0 or h_mm <= 0 or er <= 0:
        return 0.0
    val = (87.0 / math.sqrt(er + 1.41)) * math.log((5.98 * h_mm) / (0.8 * w_mm + t_mm))
    return max(0.0, val)


def _analog_digital_ground_pairs(nets: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Pairs of (analog-ground, digital-ground) net names present on the
    board — the partitions KC050 checks for a single-point tie."""
    names = {n.get("name") or "" for n in nets}
    pairs: list[tuple[str, str]] = []
    for a, d in (("AGND", "DGND"), ("GNDA", "GNDD"), ("GND_A", "GND_D")):
        if a in names and d in names:
            pairs.append((a, d))
    return pairs


def _min_pad_pitch(pads: list[dict[str, Any]]) -> float | None:
    """Minimum center-to-center distance between any two pads, mm.

    For a regular ball grid this is the ball pitch. Returns `None` when
    there are fewer than two positioned pads.
    """
    pts: list[tuple[float, float]] = []
    for p in pads:
        pos = p.get("position_mm")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            pts.append((float(pos[0]), float(pos[1])))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    best: float | None = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dx = pts[j][0] - pts[i][0]
            if best is not None and dx >= best:
                break
            dy = pts[i][1] - pts[j][1]
            d = (dx * dx + dy * dy) ** 0.5
            if d > 0.0 and (best is None or d < best):
                best = d
    return best


def _is_bga_footprint(lib_id: str, pads: list[dict[str, Any]]) -> bool:
    """Heuristic BGA detector.

    A footprint is treated as a BGA when its `lib_id` names a BGA
    package, or when it carries a dense, uniform pad array (>= 9 pads
    spread over >= 3 distinct X and >= 3 distinct Y positions — i.e. a
    grid, not a single row/column or a discrete part).
    """
    if "BGA" in lib_id.upper():
        return True
    if len(pads) < 9:
        return False
    xs: set[float] = set()
    ys: set[float] = set()
    for p in pads:
        pos = p.get("position_mm")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            xs.add(round(float(pos[0]), 3))
            ys.add(round(float(pos[1]), 3))
    return len(xs) >= 3 and len(ys) >= 3


__all__ = ["kc_validate"]
