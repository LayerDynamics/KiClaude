"""MCP tool implementations as plain functions.

These are imported and wrapped by ``server.py`` so FastMCP can expose
them over MCP, *and* imported directly by tests so the contract can be
verified without spinning up a transport.

Hard rules every function here obeys:
  - returns a JSON-serializable ``dict``
  - never raises for "expected" user errors — surface them as issues
  - every dict has a top-level boolean-ish status (``ok`` or ``in_milestone``)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ki_mcp_pcb_core import __version__ as core_version
from ki_mcp_pcb_core.cir.models import CIR_VERSION
from ki_mcp_pcb_core.cir.validation import validate_board
from ki_mcp_pcb_core.parsers.yaml import parse_yaml
from ki_mcp_pcb_core.pipeline import build as _pipeline_build
from ki_mcp_pcb_core.pipeline import doctor as _pipeline_doctor

SERVER_VERSION = "0.0.1"


def tool_version() -> dict[str, str]:
    """Return server, core library, and CIR schema versions."""
    return {
        "server_version": SERVER_VERSION,
        "core_version": core_version,
        "cir_version": CIR_VERSION,
    }


def tool_validate_cir(source_path: str) -> dict[str, Any]:
    """Run structural CIR validation on a YAML/.ato spec file."""
    path = Path(source_path)
    if not path.exists():
        return {
            "ok": False,
            "issues": [
                {"severity": "error", "code": "FS001", "message": f"file not found: {source_path}"}
            ],
        }

    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            board = parse_yaml(path)
        except Exception as exc:
            return {
                "ok": False,
                "issues": [{"severity": "error", "code": "PARSE001", "message": str(exc)}],
            }
    else:
        return {
            "ok": False,
            "issues": [
                {
                    "severity": "error",
                    "code": "PARSE002",
                    "message": f"unsupported source type {path.suffix!r} (M0 supports .yaml only)",
                }
            ],
        }

    return validate_board(board).model_dump()


def _milestone_stub(milestone: str, message: str) -> dict[str, Any]:
    return {"ok": False, "milestone": milestone, "message": message}


def tool_parse_intent(text: str, draft_path: str | None = None) -> dict[str, Any]:
    """Natural language → CIR via Claude.

    Requires ``ANTHROPIC_API_KEY`` in the environment. Writes the draft
    YAML to ``draft_path`` (if given) so the user can review the DSL
    before any KiCad files are touched.
    """
    from pathlib import Path as _P

    from ki_mcp_pcb_core.parsers.nl import (
        NLParserError,
        NLParserUnavailableError,
        parse_nl,
    )

    if not text or not text.strip():
        return {"ok": False, "error": "empty prompt"}

    try:
        result = parse_nl(text, draft_path=_P(draft_path) if draft_path else None)
    except NLParserUnavailableError as exc:
        return {"ok": False, "unavailable": True, "error": str(exc)}
    except NLParserError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "board": result.board.model_dump(),
        "draft_yaml": result.draft_yaml,
        "draft_path": draft_path,
    }


def tool_synthesize(source_path: str, out_dir: str) -> dict[str, Any]:
    """CIR source → KiCad project skeleton (.kicad_pro + .net + .kicad_pcb)."""
    from pathlib import Path as _P

    from ki_mcp_pcb_core.parsers.ato import parse_ato
    from ki_mcp_pcb_core.synthesis import synthesize as _synth

    src = _P(source_path)
    if not src.exists():
        return {"ok": False, "error": f"file not found: {source_path}"}
    try:
        if src.suffix.lower() in {".yaml", ".yml"}:
            board = parse_yaml(src)
        elif src.suffix.lower() == ".ato":
            board = parse_ato(src)
        else:
            return {"ok": False, "error": f"unknown source extension: {src.suffix!r}"}
        result = _synth(board, _P(out_dir))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "project_path": str(result.project_path),
        "pcb_path": str(result.pcb_path),
        "netlist_path": str(result.netlist_path),
    }


def tool_place(_pcb_path: str, _hints: list[str] | None = None) -> dict[str, Any]:
    return _milestone_stub("M1", "place lands in M1.")


def tool_autoplace(
    source_path: str,
    board_width_mm: float = 50.0,
    board_height_mm: float = 40.0,
    spacing_mm: float = 15.0,
) -> dict[str, Any]:
    """Push hint-driven placements to a live KiCad PCB via the IPC API.

    Requires kipy (``uv sync --extra ipc`` in the core package) plus a
    KiCad 9+ instance with IPC enabled and a board open. The function
    never raises — every outcome is reported via the structured ``code``
    field so the MCP client can branch on it.
    """
    from ki_mcp_pcb_core.placement.kipy_placer import autoplace_board

    board, err = _load_board(source_path)
    if board is None:
        return {"ok": False, "error": err}

    status = autoplace_board(
        board,
        board_width_mm=board_width_mm,
        board_height_mm=board_height_mm,
        spacing_mm=spacing_mm,
    )
    return {
        "ok": status.ok,
        "code": status.code,
        "detail": status.detail,
        "kicad_version": status.kicad_version,
        "moved": status.moved,
        "skipped": status.skipped,
    }


def tool_route(_pcb_path: str, _router: str = "freerouting") -> dict[str, Any]:
    return _milestone_stub("M1", "route lands in M1.")


def tool_drc(_pcb_path: str) -> dict[str, Any]:
    return _milestone_stub("M0/M1", "wraps `kicad-cli pcb drc`.")


def tool_erc(_schematic_path: str) -> dict[str, Any]:
    return _milestone_stub("M0/M1", "wraps `kicad-cli sch erc`.")


def tool_export_fab(_pcb_path: str, _target: str = "jlcpcb", _out: str = "fab") -> dict[str, Any]:
    return _milestone_stub("M1", "export_fab lands in M1.")


def tool_decoupling_check(source_path: str) -> dict[str, Any]:
    """Run the M2 decoupling-coverage check (CIR030) over a CIR source.

    Returns just the CIR030 issues — useful when Claude Code wants to ask
    "is my decoupling OK?" without the full validation noise.
    """
    from pathlib import Path as _P

    src = _P(source_path)
    if not src.exists():
        return {"ok": False, "error": f"file not found: {source_path}"}
    try:
        if src.suffix.lower() in {".yaml", ".yml"}:
            board = parse_yaml(src)
        elif src.suffix.lower() == ".ato":
            from ki_mcp_pcb_core.parsers.ato import parse_ato as _ato
            board = _ato(src)
        else:
            return {"ok": False, "error": f"unknown source extension: {src.suffix!r}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    from ki_mcp_pcb_core.cir.validation import validate_board

    report = validate_board(board)
    decoupling = [i for i in report.issues if i.code == "CIR030"]
    return {
        "ok": not any(i.severity == "error" for i in decoupling),
        "issues": [i.model_dump() for i in decoupling],
        "ics_with_decoupling_declared": [
            c.refdes for c in board.components if c.decoupling_pins
        ],
    }


def _load_board(source_path: str) -> tuple[Any | None, str | None]:
    """Shared board loader used by the M2/M3 focused-check tools."""
    from pathlib import Path as _P

    src = _P(source_path)
    if not src.exists():
        return None, f"file not found: {source_path}"
    try:
        if src.suffix.lower() in {".yaml", ".yml"}:
            return parse_yaml(src), None
        if src.suffix.lower() == ".ato":
            from ki_mcp_pcb_core.parsers.ato import parse_ato as _ato
            return _ato(src), None
        return None, f"unknown source extension: {src.suffix!r}"
    except Exception as exc:
        return None, str(exc)


def tool_impedance_check(source_path: str) -> dict[str, Any]:
    """Run the M3 controlled-impedance check (CIR070) — returns achievable
    Zo per net with target_impedance_ohm declared."""
    from ki_mcp_pcb_core.cir.validation import validate_board
    from ki_mcp_pcb_core.signal_integrity import (
        differential_microstrip_impedance,
        geometry_for_net,
        microstrip_impedance,
    )

    board, err = _load_board(source_path)
    if board is None:
        return {"ok": False, "error": err}

    report = validate_board(board)
    issues = [i.model_dump() for i in report.issues if i.code == "CIR070"]

    per_net: list[dict[str, Any]] = []
    for net in board.nets:
        if net.target_impedance_ohm is None:
            continue
        geo = geometry_for_net(board, net)
        achieved = None
        if geo is not None:
            try:
                achieved = (
                    differential_microstrip_impedance(geo)
                    if net.diff_pair_with else microstrip_impedance(geo)
                )
            except ValueError:
                achieved = None
        per_net.append({
            "net": net.name,
            "target_ohm": net.target_impedance_ohm,
            "achieved_ohm": round(achieved, 2) if achieved is not None else None,
            "trace_width_mm": net.trace_width_mm,
            "trace_spacing_mm": net.trace_spacing_mm,
            "reference_plane": net.reference_plane,
        })

    return {
        "ok": not any(i["severity"] == "error" for i in issues),
        "issues": issues,
        "per_net": per_net,
    }


def tool_return_path_check(source_path: str) -> dict[str, Any]:
    """Run the M3 return-path check (CIR090)."""
    from ki_mcp_pcb_core.cir.validation import validate_board

    board, err = _load_board(source_path)
    if board is None:
        return {"ok": False, "error": err}

    report = validate_board(board)
    issues = [i.model_dump() for i in report.issues if i.code == "CIR090"]
    hs_nets = [
        {"net": n.name, "net_class": n.net_class, "reference_plane": n.reference_plane}
        for n in board.nets
        if n.net_class in {"high_speed", "differential", "rf"} or n.reference_plane
    ]
    return {
        "ok": not any(i["severity"] == "error" for i in issues),
        "issues": issues,
        "high_speed_nets": hs_nets,
    }


def tool_length_tuning(source_path: str, measurements_path: str) -> dict[str, Any]:
    """Compare measured trace lengths against declared length-match groups.

    ``measurements_path`` is the JSON emitted by ``scripts/kicad_measure_lengths.py``.
    """
    from pathlib import Path as _P

    from ki_mcp_pcb_core.signal_integrity import analyze_tuning, parse_measurements

    board, err = _load_board(source_path)
    if board is None:
        return {"ok": False, "error": err}

    mp = _P(measurements_path)
    if not mp.exists():
        return {"ok": False, "error": f"measurements file not found: {measurements_path}"}
    try:
        measurements = parse_measurements(mp)
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": f"failed to parse measurements: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"failed to parse measurements: {exc}"}

    report = analyze_tuning(board, measurements)
    return {
        "ok": report.ok,
        "groups": [
            {
                "group": g.group,
                "target_mm": g.target_mm,
                "tolerance_mm": g.tolerance_mm,
                "nets": g.nets,
                "in_tolerance": g.in_tolerance,
            }
            for g in report.groups
        ],
        "queue": [
            {
                "net": a.net,
                "group": a.group,
                "current_mm": a.current_mm,
                "target_mm": a.target_mm,
                "delta_mm": a.delta_mm,
                "direction": a.direction,
            }
            for a in report.queue
        ],
    }


def tool_diff(left_path: str, right_path: str) -> dict[str, Any]:
    """Diff two CIR sources (YAML / .ato / .kicad_pro). Returns structural diff."""
    from dataclasses import asdict
    from pathlib import Path as _P

    from ki_mcp_pcb_core.diff import diff_boards

    def _load(p: str) -> tuple[Any | None, str | None]:
        path = _P(p)
        if not path.exists():
            return None, f"file not found: {p}"
        suffix = path.suffix.lower()
        try:
            if suffix in {".yaml", ".yml"}:
                return parse_yaml(path), None
            if suffix == ".ato":
                from ki_mcp_pcb_core.parsers.ato import parse_ato as _ato
                return _ato(path), None
            if suffix == ".kicad_pro":
                from ki_mcp_pcb_core.backends.kicad import KiCadBackend
                return KiCadBackend().read_project(path), None
            return None, f"unknown source extension: {suffix!r}"
        except Exception as exc:
            return None, str(exc)

    left, err = _load(left_path)
    if left is None:
        return {"ok": False, "error": f"left: {err}"}
    right, err = _load(right_path)
    if right is None:
        return {"ok": False, "error": f"right: {err}"}

    d = diff_boards(left, right)
    return {
        "ok": True,
        "identical": d.identical,
        "summary": d.summary(),
        "name_changed": d.name_changed,
        "components_added": d.components_added,
        "components_removed": d.components_removed,
        "component_changes": [asdict(c) for c in d.component_changes],
        "nets_added": d.nets_added,
        "nets_removed": d.nets_removed,
        "net_changes": [asdict(c) for c in d.net_changes],
    }


def tool_ddr_topology_check(source_path: str) -> dict[str, Any]:
    """M4 CIR100 — DDR fly-by topology check."""
    from ki_mcp_pcb_core.cir.validation import validate_board

    board, err = _load_board(source_path)
    if board is None:
        return {"ok": False, "error": err}
    report = validate_board(board)
    issues = [i.model_dump() for i in report.issues if i.code == "CIR100"]
    fly_by_nets = [
        {"net": n.name, "order": n.fly_by_order}
        for n in board.nets if n.topology == "fly_by"
    ]
    return {
        "ok": not any(i["severity"] == "error" for i in issues),
        "issues": issues,
        "fly_by_nets": fly_by_nets,
        "ddr_signed_off": board.signoff.ddr_reviewed,
    }


def tool_bga_fanout_check(source_path: str) -> dict[str, Any]:
    """M4 CIR110 — BGA pitch / fanout feasibility check."""
    from ki_mcp_pcb_core.cir._bga_fanout import bga_fanout_for_pitch
    from ki_mcp_pcb_core.cir.validation import validate_board

    board, err = _load_board(source_path)
    if board is None:
        return {"ok": False, "error": err}
    report = validate_board(board)
    issues = [i.model_dump() for i in report.issues if i.code == "CIR110"]
    bga_components = []
    for c in board.components:
        if c.bga_pitch_mm is None:
            continue
        template = bga_fanout_for_pitch(c.bga_pitch_mm)
        bga_components.append({
            "refdes": c.refdes,
            "pitch_mm": c.bga_pitch_mm,
            "template_found": template is not None,
            "requires_hdi": template.requires_hdi if template else None,
        })
    return {
        "ok": not any(i["severity"] == "error" for i in issues),
        "issues": issues,
        "bga_components": bga_components,
        "bga_signed_off": board.signoff.bga_fanout_reviewed,
    }


def tool_partition_check(source_path: str) -> dict[str, Any]:
    """Run the M2 partition-isolation check (CIR050)."""
    from pathlib import Path as _P

    src = _P(source_path)
    if not src.exists():
        return {"ok": False, "error": f"file not found: {source_path}"}
    try:
        if src.suffix.lower() in {".yaml", ".yml"}:
            board = parse_yaml(src)
        elif src.suffix.lower() == ".ato":
            from ki_mcp_pcb_core.parsers.ato import parse_ato as _ato
            board = _ato(src)
        else:
            return {"ok": False, "error": f"unknown source extension: {src.suffix!r}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    from ki_mcp_pcb_core.cir.validation import validate_board

    report = validate_board(board)
    issues = [i for i in report.issues if i.code == "CIR050"]
    partitions: dict[str, list[str]] = {}
    for c in board.components:
        if c.partition:
            partitions.setdefault(c.partition, []).append(c.refdes)
    return {
        "ok": not any(i.severity == "error" for i in issues),
        "issues": [i.model_dump() for i in issues],
        "partitions": partitions,
    }


def tool_build(source_path: str, out_dir: str, run_route: bool = False) -> dict[str, Any]:
    """Full pipeline. Real M1 implementation."""
    from pathlib import Path as _P

    result = _pipeline_build(_P(source_path), _P(out_dir), run_route=run_route)
    return {
        "ok": result.ok,
        "out_dir": str(result.out_dir),
        "stages": [
            {"name": s.name, "ok": s.ok, "detail": s.detail} for s in result.stages
        ],
    }


def tool_doctor() -> dict[str, Any]:
    """Diagnose local KiCad / Freerouting / atopile availability."""
    checks = _pipeline_doctor()
    return {
        "ok": all(c.ok for c in checks),
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
    }


# Registry — used both by server.py to wire up MCP and by tests for contract checks.
ALL_TOOLS = {
    "pcb_version": tool_version,
    "pcb_validate_cir": tool_validate_cir,
    "pcb_parse_intent": tool_parse_intent,
    "pcb_synthesize": tool_synthesize,
    "pcb_place": tool_place,
    "pcb_route": tool_route,
    "pcb_drc": tool_drc,
    "pcb_erc": tool_erc,
    "pcb_export_fab": tool_export_fab,
    "pcb_build": tool_build,
    "pcb_doctor": tool_doctor,
    "pcb_decoupling_check": tool_decoupling_check,
    "pcb_partition_check": tool_partition_check,
    "pcb_impedance_check": tool_impedance_check,
    "pcb_return_path_check": tool_return_path_check,
    "pcb_length_tuning": tool_length_tuning,
    "pcb_ddr_topology_check": tool_ddr_topology_check,
    "pcb_bga_fanout_check": tool_bga_fanout_check,
    "pcb_diff": tool_diff,
    "pcb_autoplace": tool_autoplace,
}
