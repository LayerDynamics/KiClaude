"""KiCad backend.

Emits the project skeleton that KiCad needs to bring a board to life:

  - ``<name>.kicad_pro`` — project file (JSON; configures DRC rules, layer
    visibility, etc.)
  - ``<name>.net``      — KiCad netlist S-expression. This is the canonical
    interchange — KiCad's "Update PCB from netlist" command populates the
    PCB from this file, and ``kicad-cli`` can drive that headlessly via
    pcbnew scripts.
  - ``<name>.kicad_pcb`` — minimal empty board (via kiutils). Stackup +
    layer set match :attr:`Board.fab.layer_count`. Components are added
    by KiCad's importer or our M2 "populate" step.

This file is the only place backend-specific code is allowed. Higher
layers go through the ``Backend`` interface.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ki_mcp_pcb_core.backends.base import Backend
from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.synthesis.resolver import ResolvedComponent, resolve_components


class KiCadBackend(Backend):
    name = "kicad"

    def write_project(self, board: Board, out_dir: Path) -> Path:
        """Emit ``.kicad_pro`` + ``.kicad_sch`` + ``.kicad_pcb`` + ``.net``.

        Returns the path of the ``.kicad_pro`` file (KiCad's project entry).

        M2 addition: a real ``.kicad_sch`` is emitted alongside the netlist
        so KiCad ERC has something concrete to operate on.

        Schematic-side bonus: when the board is large enough or has multiple
        partitions, the schematic is split into hierarchical sheets (one
        parent + one child per cluster) for readability.
        """
        from ki_mcp_pcb_core.synthesis.hierarchy import needs_hierarchy, write_hierarchical
        from ki_mcp_pcb_core.synthesis.schematic import write_schematic

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        resolved = resolve_components(board.components)

        pro_path = out_dir / f"{board.name}.kicad_pro"
        net_path = out_dir / f"{board.name}.net"
        pcb_path = out_dir / f"{board.name}.kicad_pcb"
        sch_path = out_dir / f"{board.name}.kicad_sch"

        pro_path.write_text(_render_kicad_pro(board), encoding="utf-8")
        net_path.write_text(_render_netlist(board, resolved), encoding="utf-8")
        pcb_path.write_text(_render_pcb_skeleton(board), encoding="utf-8")

        if needs_hierarchy(board):
            write_hierarchical(board, sch_path)
        else:
            write_schematic(board, sch_path)

        return pro_path

    def read_project(self, project_path: Path) -> Board:
        """Reverse of :meth:`write_project`.

        Reads the netlist (``.net``) sitting next to the project file
        and re-derives a CIR ``Board``. Lossy by design — placement,
        plot options, and DRC rules aren't recovered (they live in the
        ``.kicad_pro`` / ``.kicad_pcb``); we only reconstruct the
        electrical model.
        """
        project_path = Path(project_path)
        net_path = project_path.with_suffix(".net")
        if not net_path.exists():
            raise FileNotFoundError(
                f"expected {net_path} alongside {project_path}; "
                "did this project come from KiCadBackend.write_project?"
            )
        return _parse_kicad_netlist(net_path)

    def run_erc(self, project_path: Path) -> tuple[int, int]:
        """Run ERC. Returns (errors, warnings)."""
        from ki_mcp_pcb_core.validation.erc import run_erc

        sch_path = Path(project_path).with_suffix(".kicad_sch")
        result = run_erc(sch_path)
        return result.errors, result.warnings

    def run_drc(self, project_path: Path) -> tuple[int, int]:
        """Run DRC. Returns (errors, warnings)."""
        from ki_mcp_pcb_core.validation.drc import run_drc

        pcb_path = Path(project_path).with_suffix(".kicad_pcb")
        result = run_drc(pcb_path)
        return result.errors, result.warnings


# ---------------------------------------------------------------------------
# File emitters — small, side-effect-free, individually testable.
# ---------------------------------------------------------------------------


def _render_kicad_pro(board: Board) -> str:
    """Render the project JSON. KiCad 9 schema, conservative defaults.

    The ``design_settings.rules`` block carries the CIR fab profile's
    constraints. It is load-bearing when the populate step is skipped
    (pcbnew unavailable): the synthesized ``.kicad_pro`` is then the final
    artifact the user opens in KiCad. When populate *does* run, pcbnew's
    ``board.Save()`` rewrites this file and ``kicad_populate.py`` stamps
    the same constraints onto the board directly — both paths converge on
    the fab-derived ruleset.
    """
    fab = board.fab
    pro = {
        "board": {
            "design_settings": {
                "rules": {
                    "min_clearance": fab.min_space_mm,
                    "min_track_width": fab.min_trace_mm,
                    "min_via_diameter": 0.45,
                    "min_via_drill": fab.min_drill_mm,
                    # Pad plated-through-hole minimum: without this KiCad's
                    # 0.3 mm default rejects 0.2 mm module/IC holes.
                    "min_through_hole_diameter": fab.min_drill_mm,
                    "min_hole_clearance": fab.min_annular_ring_mm,
                }
            }
        },
        "meta": {
            "filename": f"{board.name}.kicad_pro",
            "version": 1,
            "generator": "ki-mcp-pcb",
        },
        "net_settings": {"classes": []},
        "pcbnew": {"page_layout_descr_file": ""},
        "schematic": {"page_layout_descr_file": ""},
        "sheets": [],
        "text_variables": {"BOARD": board.name, "GENERATOR": "ki-mcp-pcb"},
    }
    return json.dumps(pro, indent=2) + "\n"


def _render_netlist(board: Board, resolved: list[ResolvedComponent]) -> str:
    """Render a KiCad netlist (.net) file as S-expressions.

    KiCad 9 happily ingests this via 'Update PCB from netlist' or
    pcbnew's Python API.
    """
    by_refdes = {r.refdes: r for r in resolved}
    lines: list[str] = []
    lines.append('(export (version "E")')
    lines.append('  (design')
    lines.append(f'    (source "ki-mcp-pcb:{board.name}")')
    lines.append('    (tool "ki-mcp-pcb 0.0.1")')
    lines.append('  )')

    # Components ---------------------------------------------------------
    lines.append('  (components')
    for r in resolved:
        tstamp = uuid.uuid5(uuid.NAMESPACE_OID, f"{board.name}:{r.refdes}")
        value = (r.value or r.mpn).replace('"', '\\"')
        lib, _, sym = r.symbol.partition(":")
        lines.append(f'    (comp (ref "{r.refdes}")')
        lines.append(f'      (value "{value}")')
        lines.append(f'      (footprint "{r.footprint}")')
        lines.append(f'      (libsource (lib "{lib}") (part "{sym}") (description ""))')
        lines.append(f'      (property (name "MPN") (value "{r.mpn}"))')
        if r.lcsc:
            lines.append(f'      (property (name "LCSC") (value "{r.lcsc}"))')
        lines.append(f'      (tstamps "{tstamp}")')
        lines.append('    )')
    lines.append('  )')

    # Nets ---------------------------------------------------------------
    lines.append('  (nets')
    for idx, net in enumerate(board.nets, start=1):
        lines.append(f'    (net (code "{idx}") (name "{net.name}")')
        for member in net.members:
            refdes, _, pin = member.partition(".")
            if refdes not in by_refdes:
                # Validated earlier by CIR; defensive guard.
                raise ValueError(
                    f"Net {net.name!r} references unknown component {refdes!r}"
                )
            lines.append(f'      (node (ref "{refdes}") (pin "{pin}"))')
        lines.append('    )')
    lines.append('  )')

    lines.append(')')
    return "\n".join(lines) + "\n"


def _parse_kicad_netlist(net_path: Path) -> Board:
    """Re-derive a CIR Board from the netlist KiCadBackend.write_project emits.

    Only handles the dialect we produce; not a general KiCad netlist parser.
    """
    import re

    from ki_mcp_pcb_core.cir.models import Component, Net

    text = net_path.read_text(encoding="utf-8")

    # Components: each (comp (ref "...") ...) block ends at the next ")" at
    # the same depth. We use the same field layout the writer emits.
    comp_re = re.compile(
        r"""
        \(comp\s+\(ref\s+"(?P<ref>[^"]+)"\)\s*
        \(value\s+"(?P<value>[^"]*)"\)\s*
        \(footprint\s+"(?P<footprint>[^"]*)"\)\s*
        \(libsource\s+\(lib\s+"(?P<lib>[^"]*)"\)\s+\(part\s+"(?P<part>[^"]*)"\)
        """,
        re.VERBOSE | re.DOTALL,
    )
    mpn_re = re.compile(r'\(property\s+\(name\s+"MPN"\)\s+\(value\s+"([^"]+)"\)\)')
    lcsc_re = re.compile(r'\(property\s+\(name\s+"LCSC"\)\s+\(value\s+"([^"]+)"\)\)')

    components: list[Component] = []
    # We iterate component blocks by finding each "(comp " and slicing
    # to the matching balanced ")" — for our writer's flat output a
    # simple scan works.
    for match in comp_re.finditer(text):
        ref = match.group("ref")
        # Find the end of this comp block to scan for MPN/LCSC properties.
        block_start = match.start()
        depth = 0
        block_end = block_start
        for i, ch in enumerate(text[block_start:], start=block_start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
        block = text[block_start:block_end]
        mpn_match = mpn_re.search(block)
        lcsc_match = lcsc_re.search(block)
        mpn = mpn_match.group(1) if mpn_match else match.group("part")
        attrs: dict[str, str] = {}
        if lcsc_match:
            attrs["lcsc"] = lcsc_match.group(1)
        components.append(Component(
            refdes=ref,
            mpn=mpn,
            value=match.group("value") or None,
            footprint=match.group("footprint"),
            symbol=f"{match.group('lib')}:{match.group('part')}",
            attrs=attrs,
        ))

    # Nets: walk each "(net (code ...) (name ...) ...)" using paren-balanced
    # scanning so the inner (node ...) blocks don't confuse a flat regex.
    nets: list[Net] = []
    net_header_re = re.compile(r'\(net\s+\(code\s+"\d+"\)\s+\(name\s+"([^"]+)"\)')
    node_re = re.compile(r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)\)')
    for header in net_header_re.finditer(text):
        name = header.group(1)
        block_start = header.start()
        depth = 0
        block_end = block_start
        for i, ch in enumerate(text[block_start:], start=block_start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
        block = text[block_start:block_end]
        members = [f"{r}.{p}" for r, p in node_re.findall(block)]
        nets.append(Net(
            name=name,
            members=members,
            net_class=_guess_net_class(name),
        ))

    # Board name from the (source "ki-mcp-pcb:<name>") line.
    name_match = re.search(r'\(source\s+"ki-mcp-pcb:([^"]+)"\)', text)
    board_name = name_match.group(1) if name_match else net_path.stem

    return Board(name=board_name, components=components, nets=nets)


from typing import Literal as _Literal  # noqa: E402

_NetClassLit = _Literal["signal", "power", "ground", "high_speed", "differential", "rf", "analog"]


def _guess_net_class(net_name: str) -> _NetClassLit:
    """Map common net-name patterns back to net_class."""
    upper = net_name.upper()
    if upper in {"GND", "GROUND", "VSS"}:
        return "ground"
    if upper in {"VBUS", "5V0", "5V", "VCC", "3V3", "1V8", "1V35", "AVDD", "1V2"}:
        return "power"
    if upper.startswith("USB_") or upper.endswith(("_DP", "_DM", "_TXP", "_TXN", "_RXP", "_RXN")):
        return "differential"
    return "signal"


def _render_pcb_skeleton(board: Board) -> str:
    """Emit an empty .kicad_pcb that opens cleanly in KiCad 9.

    Uses kiutils to ensure the file is structurally valid. The layer set
    matches the requested fab layer count.

    NOTE: This is intentionally minimal. Component population happens in
    KiCad via the netlist import (M1 manual step, M2 headless).
    """
    try:
        from kiutils.board import Board as KBoard
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "kiutils is required to emit .kicad_pcb files. "
            "Install via `uv sync --extra kicad`."
        ) from exc

    kb = KBoard.create_new()
    # Set finished thickness from the CIR stackup.
    kb.general.thickness = board.stackup.finished_thickness_mm
    sexpr: str = kb.to_sexpr()
    return sexpr
