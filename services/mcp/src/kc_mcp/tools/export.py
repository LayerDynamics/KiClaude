"""`kc_export_fab` — drive gerber + drill + PnP + BOM via kiconnector
in a single call (M2-P-04). The tool returns a manifest of every file
produced under `output_dir` so the React fab-export dialog can stream
them back to the user as a zip.

This is the workhorse the `/pcb-fab` command (M2-C-06) wraps; pre-flight
DFM checks live in `kiserver/dfm.py` (M2-Q-03) and run BEFORE this
tool to surface JLC/OSHPark rule violations early.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool

from kc_mcp._envelope import envelope, error_envelope
from kc_mcp.clients import kiconnector_post

# Targets a fab operator picks from. Each is a hint that the kiconnector
# wrappers can use to nudge layer selection / drill format / filename
# conventions toward a specific board house's accept list.
_KNOWN_TARGETS = {"generic", "jlcpcb", "oshpark", "pcbway"}


@tool(
    "kc_export_fab",
    "Generate the full fab bundle for a PCB: gerbers, drill files, "
    "pick-and-place CSV, and (if a schematic is supplied) BOM. "
    "Returns {ok, target, output_dir, artifacts:{gerbers, drill, pos, bom}}. "
    "Targets: generic | jlcpcb | oshpark | pcbway. Spec FR-030..FR-032.",
    {
        "pcb_path": str,
        "sch_path": str,
        "output_dir": str,
        "target": str,
        "timeout_s": float,
    },
)
async def kc_export_fab(args: dict[str, Any]) -> dict[str, Any]:
    pcb_path = args.get("pcb_path", "")
    output_dir = args.get("output_dir", "")
    if not pcb_path or not output_dir:
        return error_envelope("`pcb_path` and `output_dir` are required")
    target = (args.get("target") or "generic").lower()
    if target not in _KNOWN_TARGETS:
        return error_envelope(f"target must be one of {sorted(_KNOWN_TARGETS)}; got {target!r}")
    timeout_s = args.get("timeout_s")
    timeout_body: dict[str, Any] = {}
    if isinstance(timeout_s, (int, float)) and timeout_s > 0:
        timeout_body["timeout_s"] = float(timeout_s)

    gerbers_body = {"pcb_path": pcb_path, "output_dir": output_dir, **timeout_body}
    drill_body = {"pcb_path": pcb_path, "output_dir": output_dir, **timeout_body}
    # PnP side hint: JLC eats "both"; OSHPark + PCBWay split sides.
    pos_side = "both" if target in {"generic", "jlcpcb"} else "front"
    pos_body = {
        "pcb_path": pcb_path,
        "output_dir": output_dir,
        "side": pos_side,
        **timeout_body,
    }

    gerbers_r, drill_r, pos_r = await asyncio.gather(
        _call("/tools/gerbers", gerbers_body),
        _call("/tools/drill", drill_body),
        _call("/tools/pos", pos_body),
        return_exceptions=True,
    )

    artifacts: dict[str, Any] = {
        "gerbers": _shape(gerbers_r),
        "drill": _shape(drill_r),
        "pos": _shape(pos_r),
    }

    sch_path = args.get("sch_path") or ""
    if sch_path:
        bom_body = {"sch_path": sch_path, "output_dir": output_dir, **timeout_body}
        bom_r = await _call("/tools/bom", bom_body)
        artifacts["bom"] = _shape(bom_r)
    else:
        artifacts["bom"] = {"ok": True, "skipped": True, "reason": "no sch_path supplied"}

    overall_ok = all(a.get("ok", False) for k, a in artifacts.items() if not a.get("skipped"))
    return envelope(
        {
            "ok": overall_ok,
            "target": target,
            "pcb_path": pcb_path,
            "sch_path": sch_path,
            "output_dir": output_dir,
            "artifacts": artifacts,
        }
    )


@tool(
    "kc_export_step",
    "Export a 3D STEP model of the board via kicad-cli (FR-033). "
    "Returns {ok, output_dir, step}. `board_only` exports the bare "
    "board (no component models).",
    {
        "pcb_path": str,
        "output_dir": str,
        "board_only": bool,
        "timeout_s": float,
    },
)
async def kc_export_step(args: dict[str, Any]) -> dict[str, Any]:
    pcb_path = args.get("pcb_path", "")
    output_dir = args.get("output_dir", "")
    if not pcb_path or not output_dir:
        return error_envelope("`pcb_path` and `output_dir` are required")
    body: dict[str, Any] = {"pcb_path": pcb_path, "output_dir": output_dir}
    if args.get("board_only"):
        body["board_only"] = True
    timeout_s = args.get("timeout_s")
    if isinstance(timeout_s, (int, float)) and timeout_s > 0:
        body["timeout_s"] = float(timeout_s)
    result = await _call("/tools/step", body)
    shaped = _shape(result)
    return envelope(
        {
            "ok": shaped.get("ok", False),
            "pcb_path": pcb_path,
            "output_dir": output_dir,
            "step": shaped,
        }
    )


async def _call(path: str, body: dict[str, Any]) -> dict[str, Any] | Exception:
    try:
        return await kiconnector_post(path, body)
    except Exception as e:
        return e


def _shape(result: dict[str, Any] | BaseException) -> dict[str, Any]:
    if isinstance(result, BaseException):
        return {"ok": False, "error": str(result), "files": []}
    return dict(result)


__all__ = ["kc_export_fab"]
