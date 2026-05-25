"""`kc_validate` — KCIR structural + M5 co-pilot validators.

Runs KCIR-only sanity checks that don't require a running kicad-cli:

- KC001..KC011 — structural integrity (refdes, footprints, nets,
  hierarchy) — M1-P-04.
- KC060 — DDR fly-by topology reaches >= 3 nodes and is human signed
  off (SPEC §7.3; warns until `pcb.signoff.ddr_reviewed`).
- KC070 — BGA fanout is feasible on the declared fab DFM rules (SPEC
  §7.3; warns until `pcb.signoff.bga_fanout_reviewed`).

Real ERC (electrical-rule-check) lives in `tools/erc.py` and shells
out to `kicad-cli sch erc`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiserver_get


@tool(
    "kc_validate",
    "Run the KCIR-level sanity validators on an opened project: "
    "KC001..KC011 structural checks plus the M5 co-pilot validators "
    "KC060 (DDR fly-by topology) and KC070 (BGA fanout feasibility). "
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

    return findings


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
