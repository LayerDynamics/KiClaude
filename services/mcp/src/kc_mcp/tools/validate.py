"""`kc_validate` — KC001..KC011 structural validators (M1-P-04).

Runs KCIR-only sanity checks that don't require a running kicad-cli.
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
    "Run KC001..KC011 KCIR-level sanity validators on an opened "
    "project. Returns a list of findings with `code`, `severity`, "
    "`message`, and optional `target_uuid`. Read-only.",
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

    return findings


__all__ = ["kc_validate"]
