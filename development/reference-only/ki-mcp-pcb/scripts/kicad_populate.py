#!/usr/bin/env python3
"""Populate a KiCad PCB from our netlist.

Runs **inside KiCad's bundled Python** (or any environment where
``pcbnew`` is importable). Reads a KiCad netlist (``.net``) and an
existing empty ``.kicad_pcb`` skeleton, instantiates each component's
footprint via the KiCad footprint-library lookup, applies net
connections, and grid-places the result.

This script is the missing link that turns the M1 pipeline from
"synthesize project + ask user to import netlist" into
"text → routed manufacturable PCB" autonomously.

Implementation note — why we don't use ``BOARD_NETLIST_UPDATER``:
KiCad 8/9/10 dropped the netlist reader/updater classes
(``NETLIST``, ``NETLIST_READER``, ``BOARD_NETLIST_UPDATER``) from the
``pcbnew`` SWIG bindings. We therefore parse the netlist ourselves —
the ``.net`` file is produced by our own synthesizer, so its grammar
is fully under our control — and apply it with the primitives that
*are* still exposed: ``FootprintLoad``, ``NETINFO_ITEM`` and per-pad
``SetNet``.

Usage:
    kicad-cli has no built-in "update from netlist", so we invoke this
    directly with KiCad's Python:

        $KICAD_PYTHON scripts/kicad_populate.py \\
            --pcb path/to/board.kicad_pcb \\
            --net path/to/board.net \\
            [--placement grid] \\
            [--spacing-mm 15.0] \\
            [--report report.json]

Exit codes:
    0  success
    2  pcbnew not importable (run under KiCad's bundled Python)
    3  netlist parse failure
    4  PCB load / save failure
    5  footprint(s) not found, or a net references a missing pad
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _import_pcbnew():
    try:
        import pcbnew  # type: ignore[import-not-found]
    except ImportError as exc:
        sys.stderr.write(
            f"pcbnew not importable: {exc}\n"
            "Run this script under KiCad's bundled Python (e.g. on Linux:\n"
            "  /usr/lib/kicad/bin/python3 scripts/kicad_populate.py ...)\n"
        )
        sys.exit(2)
    return pcbnew


# ---------------------------------------------------------------------------
# Per-run report
# ---------------------------------------------------------------------------


@dataclass
class PopulateReport:
    ok: bool = True
    pcb_path: str = ""
    netlist_path: str = ""
    components_placed: int = 0
    footprints_missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "pcb_path": self.pcb_path,
            "netlist_path": self.netlist_path,
            "components_placed": self.components_placed,
            "footprints_missing": list(self.footprints_missing),
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Netlist S-expression parsing
#
# KiCad netlists are S-expressions:
#   (export (version "E")
#     (components (comp (ref "U1") (value "X") (footprint "Lib:Fp") ...))
#     (nets (net (code "1") (name "GND") (node (ref "U1") (pin "1")) ...)))
# We parse them into nested Python lists of strings, then pull out the
# component and net structure with small accessor helpers.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NetlistComponent:
    ref: str
    value: str
    footprint: str  # KiCad "Library:Name" identifier ("" if unspecified)


@dataclass(frozen=True)
class NetlistNode:
    ref: str
    pin: str


@dataclass(frozen=True)
class NetlistNet:
    name: str
    nodes: list[NetlistNode]


@dataclass(frozen=True)
class ParsedNetlist:
    components: list[NetlistComponent]
    nets: list[NetlistNet]


class NetlistParseError(RuntimeError):
    """The .net file could not be parsed as a KiCad netlist."""


def _tokenize(text: str) -> list[str | tuple[str, str]]:
    """Split an S-expression into ``(``, ``)`` and value tokens.

    Value tokens are ``("v", string)`` tuples so a bare atom and a
    quoted string are handled uniformly downstream. KiCad quotes with
    ``"`` and escapes embedded quotes/backslashes with ``\\``.
    """
    tokens: list[str | tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            tokens.append(c)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            i += 1
            buf: list[str] = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            if i >= n:
                raise NetlistParseError("unterminated quoted string")
            i += 1  # consume closing quote
            tokens.append(("v", "".join(buf)))
        else:
            buf = []
            while i < n and not text[i].isspace() and text[i] not in '()"':
                buf.append(text[i])
                i += 1
            tokens.append(("v", "".join(buf)))
    return tokens


def _parse_sexpr(text: str) -> list:
    """Parse a single top-level S-expression into nested lists."""
    tokens = _tokenize(text)
    pos = 0

    def parse_node() -> str | list:
        nonlocal pos
        if pos >= len(tokens):
            raise NetlistParseError("unexpected end of input")
        tok = tokens[pos]
        if tok == "(":
            pos += 1
            node: list = []
            while pos < len(tokens) and tokens[pos] != ")":
                node.append(parse_node())
            if pos >= len(tokens):
                raise NetlistParseError("unbalanced parentheses")
            pos += 1  # consume ")"
            return node
        if tok == ")":
            raise NetlistParseError("unexpected ')'")
        pos += 1
        return tok[1]  # ("v", value) -> value

    root = parse_node()
    if not isinstance(root, list):
        raise NetlistParseError("netlist root is not an S-expression list")
    return root


def _children(node: list, key: str) -> list[list]:
    """Return every child list of ``node`` whose head atom equals ``key``."""
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def _child(node: list, key: str) -> list | None:
    matches = _children(node, key)
    return matches[0] if matches else None


def _value(node: list, key: str) -> str:
    """Return the string argument of ``(key "value")`` inside ``node``."""
    c = _child(node, key)
    if c is None or len(c) < 2 or not isinstance(c[1], str):
        return ""
    return c[1]


def parse_netlist(text: str) -> ParsedNetlist:
    """Parse a KiCad ``.net`` file into typed components + nets."""
    root = _parse_sexpr(text)
    if not root or root[0] != "export":
        raise NetlistParseError(
            f"expected an (export ...) netlist, got {root[0] if root else 'empty'!r}"
        )

    components: list[NetlistComponent] = []
    comp_section = _child(root, "components")
    if comp_section is not None:
        for comp in _children(comp_section, "comp"):
            components.append(
                NetlistComponent(
                    ref=_value(comp, "ref"),
                    value=_value(comp, "value"),
                    footprint=_value(comp, "footprint"),
                )
            )

    nets: list[NetlistNet] = []
    nets_section = _child(root, "nets")
    if nets_section is not None:
        for net in _children(nets_section, "net"):
            nodes = [
                NetlistNode(ref=_value(node, "ref"), pin=_value(node, "pin"))
                for node in _children(net, "node")
            ]
            nets.append(NetlistNet(name=_value(net, "name"), nodes=nodes))

    return ParsedNetlist(components=components, nets=nets)


# ---------------------------------------------------------------------------
# Footprint library discovery
#
# Footprints live in ``<Library>.pretty/`` directories. We search the
# same kinds of locations as ki_mcp_pcb_core.synthesis.sym_lib does for
# symbols, but this script is standalone (it runs under KiCad's bundled
# Python, where the project package isn't installed), so the search
# logic is duplicated here deliberately.
# ---------------------------------------------------------------------------

# Our own override, plus the env vars KiCad itself sets.
_FOOTPRINT_ENV_VARS: tuple[str, ...] = (
    "KICAD_FOOTPRINTS",
    "KICAD10_FOOTPRINT_DIR",
    "KICAD9_FOOTPRINT_DIR",
    "KICAD8_FOOTPRINT_DIR",
)

_DEFAULT_FOOTPRINT_SEARCH_PATHS: tuple[str, ...] = (
    "/usr/share/kicad/footprints",
    "/usr/local/share/kicad/footprints",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
    "C:/Program Files/KiCad/10.0/share/kicad/footprints",
    "C:/Program Files/KiCad/9.0/share/kicad/footprints",
    "C:/Program Files/KiCad/8.0/share/kicad/footprints",
)


def find_footprint_lib_paths() -> list[Path]:
    """Return existing directories that hold ``*.pretty`` libraries.

    Earlier entries win: an env override is consulted before the stock
    install locations, and a project-local ``libs/`` last.
    """
    candidates: list[Path] = []
    for env_var in _FOOTPRINT_ENV_VARS:
        override = os.environ.get(env_var)
        if override:
            candidates.append(Path(override))
    candidates.extend(Path(p) for p in _DEFAULT_FOOTPRINT_SEARCH_PATHS)
    local = Path.cwd() / "libs"
    if local.is_dir():
        candidates.append(local)

    seen: set[Path] = set()
    paths: list[Path] = []
    for c in candidates:
        if c.is_dir() and c not in seen:
            seen.add(c)
            paths.append(c)
    return paths


def load_footprint(pcbnew, search_paths: list[Path], footprint_id: str):
    """Load the ``"Library:Name"`` footprint, or return ``None``.

    ``pcbnew.FootprintLoad`` returns ``None`` for an unknown footprint
    name inside an existing ``.pretty`` directory, and raises for a
    missing directory — we guard with ``is_dir()`` and also catch, so
    a single bad library can't abort the whole populate run.
    """
    lib_name, _, fp_name = footprint_id.partition(":")
    if not lib_name or not fp_name:
        return None
    for base in search_paths:
        pretty = base / f"{lib_name}.pretty"
        if not pretty.is_dir():
            continue
        try:
            footprint = pcbnew.FootprintLoad(str(pretty), fp_name)
        except Exception:  # a single bad library must not abort the run
            footprint = None
        if footprint is not None:
            return footprint
    return None


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def _grid_positions(count: int, spacing_mm: float) -> list[tuple[float, float]]:
    """Return ``count`` positions (in mm) on a tight grid around origin."""
    if count <= 0:
        return []
    cols = max(1, math.ceil(math.sqrt(count)))
    positions: list[tuple[float, float]] = []
    for i in range(count):
        row, col = divmod(i, cols)
        positions.append((col * spacing_mm + 20.0, row * spacing_mm + 20.0))
    return positions


# ---------------------------------------------------------------------------
# Design rules + board outline
#
# A bare KiCad skeleton carries no design rules and no board edge, so DRC
# rejects it (default 0.3 mm min drill vs. 0.2 mm module holes; "malformed
# outline"). The pipeline hands us the CIR fab profile and outline via a
# small ``--design-json`` sidecar; we push both onto the board here, and
# ``board.Save()`` persists them (rules land in the sibling .kicad_pro).
# ---------------------------------------------------------------------------

# Margin (mm) added around the placed-component bounding box when the CIR
# outline is "auto" — the board edge is fitted to whatever got placed.
_AUTO_OUTLINE_MARGIN_MM = 5.0

# Edge.Cuts line width (mm) — KiCad's conventional board-edge stroke.
_EDGE_CUTS_WIDTH_MM = 0.1


def apply_design_rules(pcbnew, board, fab: dict) -> None:
    """Push the CIR fab profile's constraints onto the board.

    Without this the board keeps KiCad's stock defaults — notably a
    0.3 mm minimum through-hole drill, which rejects the 0.2 mm holes
    used by common module/IC footprints.
    """
    settings = board.GetDesignSettings()
    rule_map = (
        ("min_trace_mm", "m_TrackMinWidth"),
        ("min_space_mm", "m_MinClearance"),
        ("min_drill_mm", "m_MinThroughDrill"),
        ("min_annular_ring_mm", "m_ViasMinAnnularWidth"),
    )
    for key, attr in rule_map:
        value = fab.get(key)
        if value is not None and hasattr(settings, attr):
            setattr(settings, attr, pcbnew.FromMM(float(value)))


def _placed_extent_mm(pcbnew, board) -> tuple[float, float, float, float]:
    """Return ``(x0, y0, x1, y1)`` mm — the bounding box of placed content."""
    bbox = board.GetBoundingBox()
    return (
        pcbnew.ToMM(bbox.GetLeft()),
        pcbnew.ToMM(bbox.GetTop()),
        pcbnew.ToMM(bbox.GetRight()),
        pcbnew.ToMM(bbox.GetBottom()),
    )


def _add_edge_segment(
    pcbnew, board, x0: float, y0: float, x1: float, y1: float
) -> None:
    """Add one Edge.Cuts line segment between two mm coordinates."""
    seg = pcbnew.PCB_SHAPE(board)
    seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
    seg.SetLayer(pcbnew.Edge_Cuts)
    seg.SetWidth(pcbnew.FromMM(_EDGE_CUTS_WIDTH_MM))
    seg.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
    seg.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
    board.Add(seg)


def draw_board_outline(pcbnew, board, outline: dict) -> None:
    """Draw a closed Edge.Cuts board outline.

    Honors the CIR ``Outline``: an explicit ``rect`` (width/height,
    centered on the placed content) or ``polygon`` (vertex loop) is
    drawn verbatim; ``auto`` — or any under-specified outline — fits a
    rectangle to the placed-component bounding box plus a fixed margin.

    Corner rounding (``corner_radius_mm``) is not applied: a sharp
    rectangular edge is drawn, which is fully manufacturable and DRC-clean.
    """
    shape = outline.get("shape", "auto")
    polygon = outline.get("polygon_mm")
    width = outline.get("width_mm")
    height = outline.get("height_mm")

    if shape == "polygon" and polygon and len(polygon) >= 3:
        points = [(float(px), float(py)) for px, py in polygon]
        for i in range(len(points)):
            x0, y0 = points[i]
            x1, y1 = points[(i + 1) % len(points)]
            _add_edge_segment(pcbnew, board, x0, y0, x1, y1)
        return

    if shape == "rect" and width and height:
        # Center the declared board rectangle on the placed content.
        ext_x0, ext_y0, ext_x1, ext_y1 = _placed_extent_mm(pcbnew, board)
        cx = (ext_x0 + ext_x1) / 2.0
        cy = (ext_y0 + ext_y1) / 2.0
        x0, y0 = cx - float(width) / 2.0, cy - float(height) / 2.0
        x1, y1 = cx + float(width) / 2.0, cy + float(height) / 2.0
    else:
        # "auto" (or rect/polygon missing its dimensions): fit to content.
        ext_x0, ext_y0, ext_x1, ext_y1 = _placed_extent_mm(pcbnew, board)
        margin = _AUTO_OUTLINE_MARGIN_MM
        x0, y0 = ext_x0 - margin, ext_y0 - margin
        x1, y1 = ext_x1 + margin, ext_y1 + margin
        if x1 - x0 < 1.0 or y1 - y0 < 1.0:
            # Degenerate (e.g. nothing placed) — emit a sane default board.
            x0, y0, x1, y1 = 0.0, 0.0, 20.0, 20.0

    rect = pcbnew.PCB_SHAPE(board)
    rect.SetShape(pcbnew.SHAPE_T_RECT)
    rect.SetLayer(pcbnew.Edge_Cuts)
    rect.SetWidth(pcbnew.FromMM(_EDGE_CUTS_WIDTH_MM))
    rect.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
    rect.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
    board.Add(rect)


def _load_design_spec(path: Path | None) -> dict:
    """Read the ``--design-json`` sidecar. Returns ``{}`` when absent.

    The pipeline writes this file immediately before invoking us; a
    missing or malformed file is non-fatal — populate proceeds with an
    auto-fitted outline and KiCad's default design rules.
    """
    if path is None:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Populate a KiCad PCB from a netlist.")
    parser.add_argument("--pcb", required=True, type=Path)
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--placement", default="grid", choices=["grid"])
    parser.add_argument("--spacing-mm", type=float, default=15.0)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--design-json",
        type=Path,
        default=None,
        help="Sidecar JSON carrying the CIR fab profile + board outline.",
    )
    args = parser.parse_args(argv)

    design = _load_design_spec(args.design_json)

    pcbnew = _import_pcbnew()

    report = PopulateReport(
        pcb_path=str(args.pcb),
        netlist_path=str(args.net),
    )

    # 1. Load the board ------------------------------------------------
    try:
        board = pcbnew.LoadBoard(str(args.pcb))
    except Exception as exc:  # surface any pcbnew failure as a structured report
        report.ok = False
        report.errors.append(f"failed to load PCB: {exc}")
        _write_report(report, args.report)
        return 4

    # 2. Parse the netlist --------------------------------------------
    try:
        netlist = parse_netlist(args.net.read_text(encoding="utf-8"))
    except (OSError, NetlistParseError) as exc:
        report.ok = False
        report.errors.append(f"netlist parse failure: {exc}")
        _write_report(report, args.report)
        return 3

    # 3. Instantiate footprints ---------------------------------------
    search_paths = find_footprint_lib_paths()
    placed: list[tuple[object, str]] = []  # (footprint, refdes)
    for comp in netlist.components:
        ref = comp.ref or "?"
        if not comp.footprint:
            report.footprints_missing.append(f"{ref} (no footprint assigned)")
            continue
        footprint = load_footprint(pcbnew, search_paths, comp.footprint)
        if footprint is None:
            report.footprints_missing.append(f"{ref} ({comp.footprint})")
            continue
        footprint.SetReference(ref)
        footprint.SetValue(comp.value)
        board.Add(footprint)
        placed.append((footprint, ref))

    report.components_placed = len(placed)

    # 4. Place what we instantiated -----------------------------------
    # Hint-aware coordinates from the design sidecar (placement.plan_placement)
    # take priority; any refdes without one falls back to a blind grid slot.
    # Index-based iteration pairs `placed` with the grid 1:1 — KiCad's
    # bundled Python is 3.9, so no zip(strict=...).
    planned = design.get("placements")
    planned = planned if isinstance(planned, dict) else {}
    grid = _grid_positions(len(placed), args.spacing_mm)
    for index, (footprint, ref) in enumerate(placed):
        hinted = planned.get(ref)
        if isinstance(hinted, list) and len(hinted) == 2:
            x_mm, y_mm = float(hinted[0]), float(hinted[1])
        else:
            x_mm, y_mm = grid[index]
        footprint.SetPosition(
            pcbnew.VECTOR2I(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm))
        )

    if report.footprints_missing:
        report.ok = False
        report.errors.append(
            f"{len(report.footprints_missing)} footprint(s) not found in any "
            f"library under {[str(p) for p in search_paths]}"
        )
        _write_report(report, args.report)
        return 5

    # 5. Draw the board outline ---------------------------------------
    # Runs after placement so an "auto" outline can fit the components.
    try:
        draw_board_outline(pcbnew, board, design.get("outline") or {})
    except Exception as exc:  # surface any pcbnew failure as a structured report
        report.ok = False
        report.errors.append(f"failed to draw board outline: {exc}")
        _write_report(report, args.report)
        return 4

    # 6. Apply net connectivity ----------------------------------------
    # Net codes are assigned sequentially (1..N): KiCad recomputes them
    # on load anyway, and self-assigning guarantees they're unique and
    # non-zero regardless of what the .net file's (code ...) said.
    for net_code, net in enumerate(netlist.nets, start=1):
        netinfo = pcbnew.NETINFO_ITEM(board, net.name, net_code)
        board.Add(netinfo)
        for node in net.nodes:
            footprint = board.FindFootprintByReference(node.ref)
            if footprint is None:
                report.errors.append(
                    f"net '{net.name}' references unknown component '{node.ref}'"
                )
                continue
            pad = footprint.FindPadByNumber(node.pin)
            if pad is None:
                report.errors.append(
                    f"net '{net.name}': component '{node.ref}' has no pad "
                    f"'{node.pin}'"
                )
                continue
            pad.SetNet(netinfo)

    if report.errors:
        report.ok = False
        _write_report(report, args.report)
        return 5

    # 7. Apply fab design rules ---------------------------------------
    # board.Save() persists these into the sibling .kicad_pro.
    try:
        apply_design_rules(pcbnew, board, design.get("fab") or {})
    except Exception as exc:  # surface any pcbnew failure as a structured report
        report.ok = False
        report.errors.append(f"failed to apply design rules: {exc}")
        _write_report(report, args.report)
        return 4

    # 8. Save ---------------------------------------------------------
    try:
        board.Save(str(args.pcb))
    except Exception as exc:  # surface any pcbnew failure as a structured report
        report.ok = False
        report.errors.append(f"failed to save PCB: {exc}")
        _write_report(report, args.report)
        return 4

    report.ok = True
    _write_report(report, args.report)
    return 0


def _write_report(report: PopulateReport, path: Path | None) -> None:
    text = json.dumps(report.to_dict(), indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
