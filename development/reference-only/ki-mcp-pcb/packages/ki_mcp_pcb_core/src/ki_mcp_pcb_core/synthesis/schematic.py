"""CIR → KiCad schematic synthesis.

Emits a KiCad-10 ``.kicad_sch`` (file-format version ``20250114``)
directly as S-expressions. Each component becomes a placed symbol whose
library definition is embedded in ``lib_symbols`` (copied verbatim from
the stock ``.kicad_sym`` files — a schematic's ``lib_symbols`` entry uses
the identical grammar). Electrical connectivity is expressed with net
labels dropped on the pin connection points: two pins on the same net
carry a label with the same text and are therefore connected. Pins that
aren't on any net get a no-connect flag so ERC stays clean, and every
power/ground net gets a ``PWR_FLAG`` so ERC's "power input not driven"
check is satisfied.

Why hand-written S-expressions instead of kiutils: kiutils only writes
the KiCad-6 schematic format (``version 20211014``), which KiCad 9/10's
``kicad-cli sch erc`` refuses to load. This module targets the current
format so ERC actually runs.

Pin geometry: a KiCad symbol library stores pin coordinates with +Y up;
a schematic uses +Y down. A symbol placed unrotated at ``(sx, sy)``
therefore puts a library pin at ``(px, py)`` on the schematic at
``(sx + px, sy - py)``. Symbol origins are snapped to the 1.27 mm
connection grid so pin ends land on-grid.
"""

from __future__ import annotations

import re
import uuid as _uuid
from functools import lru_cache
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board
from ki_mcp_pcb_core.synthesis.resolver import resolve_components
from ki_mcp_pcb_core.synthesis.sym_lib import SymbolLibIndex, default_index

# KiCad 10 schematic file-format version (the value eeschema writes).
_SCH_FORMAT_VERSION = "20250114"

# KiCad's connection grid; symbol origins snap to it so pins stay on-grid.
_GRID_MM = 1.27

# Horizontal pitch between placed components. Generous so even a large
# multi-pin module's pin fan-out can't overlap its neighbour.
_COMPONENT_PITCH_MM = 76.2
_COMPONENT_ROW_Y_MM = 100.0
_COMPONENT_ROW_X0_MM = 76.2

# PWR_FLAGs sit in their own row, clear of the components' pin fan-out.
_FLAG_ROW_Y_MM = 260.0
_FLAG_ROW_X0_MM = 50.0
_FLAG_PITCH_MM = 25.4

# The stock KiCad symbol that asserts a net is externally driven.
_PWR_FLAG_LIB_ID = "power:PWR_FLAG"


# ---------------------------------------------------------------------------
# S-expression primitives
# ---------------------------------------------------------------------------


def _ns(seed: str) -> str:
    """Deterministic UUID from a seed — keeps synthesis output reproducible."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_OID, seed))


def _quote(value: str) -> str:
    """Quote + escape a string as a KiCad S-expression token."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fmt(value: float) -> str:
    """Format a millimetre coordinate the way KiCad does — no float noise."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _snap(value: float) -> float:
    """Snap a coordinate to KiCad's 1.27 mm connection grid."""
    return round(value / _GRID_MM) * _GRID_MM


# ---------------------------------------------------------------------------
# Library-symbol extraction
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _read_symbol_lib(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8")


def _balanced_block(text: str, start: int) -> str | None:
    """Return the S-expression beginning at index ``start`` (must be a '(')."""
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
        elif char == '"':
            in_str = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_lib_symbol(
    lib_id: str,
    search_paths: list[Path],
    _seen: frozenset[str] | None = None,
) -> tuple[str, str] | None:
    """Resolve ``lib_id`` to a self-contained ``(symbol …)`` definition.

    Returns ``(effective_lib_id, block)``: the block is copied verbatim
    from the stock ``.kicad_sym`` file with its top-level name rewritten
    to ``effective_lib_id`` (nested unit sub-symbols keep their bare
    names, as KiCad expects).

    Many library symbols are *derived* — ``(extends "Base")`` — and carry
    no pins of their own; ``Base`` holds the geometry and is electrically
    identical (extends is KiCad's pin-compatible-variant mechanism). We
    follow the chain to that self-contained root, so the schematic embeds
    a complete symbol KiCad can load and ERC. Returns ``None`` when the
    library or symbol can't be found.
    """
    seen = _seen or frozenset()
    if lib_id in seen:  # defensive: a cyclic extends chain
        return None
    lib_name, _, sym_name = lib_id.partition(":")
    if not lib_name or not sym_name:
        return None
    for base in search_paths:
        lib_file = base / f"{lib_name}.kicad_sym"
        if not lib_file.is_file():
            continue
        text = _read_symbol_lib(str(lib_file))
        needle = f'(symbol "{sym_name}"'
        idx = text.find(needle)
        if idx == -1:
            continue
        block = _balanced_block(text, idx)
        if block is None:
            continue
        extends = re.search(r'\(extends "([^"]+)"\)', block)
        if extends is not None:
            return extract_lib_symbol(
                f"{lib_name}:{extends.group(1)}", search_paths, seen | {lib_id}
            )
        return (lib_id, block.replace(needle, f'(symbol "{lib_id}"', 1))
    return None


def _pin_electrical_types(block: str) -> dict[str, str]:
    """Map pin number → electrical type from a symbol definition block.

    Pin electrical type is the first token of a ``(pin <type> <style> …)``
    form — e.g. ``power_out``, ``power_in``, ``passive``, ``output``.
    """
    types: dict[str, str] = {}
    search = 0
    while True:
        idx = block.find("(pin ", search)
        if idx == -1:
            break
        search = idx + 5
        pin_block = _balanced_block(block, idx)
        if pin_block is None:
            continue
        head = re.match(r"\(pin\s+(\S+)", pin_block)
        number = re.search(r'\(number "([^"]+)"', pin_block)
        if head is not None and number is not None:
            types[number.group(1)] = head.group(1)
    return types


def undriven_power_net_names(
    board: Board, *, symbol_index: SymbolLibIndex | None = None
) -> list[str]:
    """Power/ground net names that need a ``PWR_FLAG``.

    A power/ground net is "driven" when one of its pins is a power-output
    (a regulator output, an MCU's internal-LDO ``VCAP`` pin, …). Such a
    net already satisfies ERC's "power input not driven" check — adding a
    ``PWR_FLAG`` (itself a power output) would instead raise a power-output
    conflict. Returns only the *undriven* nets, which genuinely need one.
    """
    index = symbol_index if symbol_index is not None else default_index()
    search_paths = index.search_paths
    symbol_by_refdes = {r.refdes: r.symbol for r in resolve_components(board.components)}
    types_cache: dict[str, dict[str, str]] = {}

    def _pin_types(symbol_id: str) -> dict[str, str]:
        if symbol_id not in types_cache:
            result = extract_lib_symbol(symbol_id, search_paths)
            types_cache[symbol_id] = (
                _pin_electrical_types(result[1]) if result is not None else {}
            )
        return types_cache[symbol_id]

    undriven: list[str] = []
    for net in board.nets:
        if net.net_class not in ("power", "ground"):
            continue
        driven = False
        for member in net.members:
            refdes, _, pin = member.partition(".")
            symbol_id = symbol_by_refdes.get(refdes)
            if symbol_id is not None and _pin_types(symbol_id).get(pin) == "power_out":
                driven = True
                break
        if not driven:
            undriven.append(net.name)
    return undriven


# ---------------------------------------------------------------------------
# A symbol to place on the sheet — a CIR component or a synthetic PWR_FLAG.
# ---------------------------------------------------------------------------


class _PlacedSymbol:
    """One symbol instance to emit, plus the data its pins need."""

    def __init__(
        self,
        *,
        lib_id: str,
        refdes: str,
        value: str,
        footprint: str,
        mpn: str,
        in_bom: bool,
        x: float,
        y: float,
        pin_map: dict[str, tuple[float, float]],
        is_power_flag: bool = False,
    ) -> None:
        self.lib_id = lib_id
        self.refdes = refdes
        self.value = value
        self.footprint = footprint
        self.mpn = mpn
        self.in_bom = in_bom
        self.x = x
        self.y = y
        self.pin_map = pin_map
        self.is_power_flag = is_power_flag


# ---------------------------------------------------------------------------
# Element emitters
# ---------------------------------------------------------------------------


def _emit_property(
    key: str, value: str, x: float, y: float, *, hide: bool
) -> list[str]:
    """Emit a ``(property …)`` block for a placed symbol."""
    lines = [
        f"\t\t(property {_quote(key)} {_quote(value)}",
        f"\t\t\t(at {_fmt(x)} {_fmt(y)} 0)",
        "\t\t\t(effects",
        "\t\t\t\t(font",
        "\t\t\t\t\t(size 1.27 1.27)",
        "\t\t\t\t)",
    ]
    if hide:
        lines.append("\t\t\t\t(hide yes)")
    lines.append("\t\t\t)")
    lines.append("\t\t)")
    return lines


def _emit_symbol(sym: _PlacedSymbol, project: str, sch_uuid: str) -> str:
    """Emit a placed ``(symbol …)`` instance."""
    lines = [
        "\t(symbol",
        f"\t\t(lib_id {_quote(sym.lib_id)})",
        f"\t\t(at {_fmt(sym.x)} {_fmt(sym.y)} 0)",
        "\t\t(unit 1)",
        "\t\t(exclude_from_sim no)",
        f"\t\t(in_bom {'yes' if sym.in_bom else 'no'})",
        "\t\t(on_board yes)",
        "\t\t(dnp no)",
        f"\t\t(uuid {_quote(_ns(f'{sym.refdes}:symbol'))})",
    ]
    lines += _emit_property("Reference", sym.refdes, sym.x, sym.y - 5.08, hide=False)
    lines += _emit_property("Value", sym.value, sym.x, sym.y + 5.08, hide=False)
    lines += _emit_property("Footprint", sym.footprint, sym.x, sym.y, hide=True)
    lines += _emit_property("Datasheet", "~", sym.x, sym.y, hide=True)
    if sym.mpn:
        lines += _emit_property("MPN", sym.mpn, sym.x, sym.y, hide=True)
    for pin_number in sorted(sym.pin_map):
        lines.append(f"\t\t(pin {_quote(pin_number)}")
        lines.append(
            f"\t\t\t(uuid {_quote(_ns(f'{sym.refdes}.{pin_number}:pin'))})"
        )
        lines.append("\t\t)")
    lines += [
        "\t\t(instances",
        f"\t\t\t(project {_quote(project)}",
        f'\t\t\t\t(path "/{sch_uuid}"',
        f"\t\t\t\t\t(reference {_quote(sym.refdes)})",
        "\t\t\t\t\t(unit 1)",
        "\t\t\t\t)",
        "\t\t\t)",
        "\t\t)",
        "\t)",
    ]
    return "\n".join(lines)


def _emit_label(net_name: str, x: float, y: float, seed: str) -> str:
    """Emit a ``(global_label …)`` — same-named labels are one net.

    Global (not local) labels are used so connectivity holds across
    hierarchical child sheets too, not just within one sheet.
    """
    return "\n".join([
        f"\t(global_label {_quote(net_name)}",
        "\t\t(shape bidirectional)",
        f"\t\t(at {_fmt(x)} {_fmt(y)} 0)",
        "\t\t(fields_autoplaced yes)",
        "\t\t(effects",
        "\t\t\t(font",
        "\t\t\t\t(size 1.27 1.27)",
        "\t\t\t)",
        "\t\t\t(justify left)",
        "\t\t)",
        f"\t\t(uuid {_quote(_ns('label:' + seed))})",
        '\t\t(property "Intersheetrefs" "${INTERSHEET_REFS}"',
        f"\t\t\t(at {_fmt(x)} {_fmt(y)} 0)",
        "\t\t\t(effects",
        "\t\t\t\t(font",
        "\t\t\t\t\t(size 1.27 1.27)",
        "\t\t\t\t)",
        "\t\t\t\t(hide yes)",
        "\t\t\t)",
        "\t\t)",
        "\t)",
    ])


def _emit_no_connect(x: float, y: float, seed: str) -> str:
    """Emit a ``(no_connect …)`` flag — marks a pin as intentionally unused."""
    return "\n".join([
        "\t(no_connect",
        f"\t\t(at {_fmt(x)} {_fmt(y)})",
        f"\t\t(uuid {_quote(_ns('nc:' + seed))})",
        "\t)",
    ])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_schematic(
    board: Board,
    out_path: Path,
    *,
    symbol_index: SymbolLibIndex | None = None,
    power_flag_nets: list[str] | None = None,
) -> Path:
    """Emit a KiCad-10 ``.kicad_sch`` derived from the CIR ``board``.

    Every component is placed as a symbol with its library definition
    embedded in ``lib_symbols``; each pin gets a net label (when on a net)
    or a no-connect flag (when not), and every power/ground net gets a
    ``PWR_FLAG`` — so ``kicad-cli sch erc`` both loads the file and passes
    it clean.

    ``power_flag_nets`` overrides which nets get a ``PWR_FLAG``: ``None``
    (the default, used for flat schematics) derives them from the board's
    power/ground nets. Hierarchical synthesis passes an explicit list so
    each project-wide power net is flagged exactly once, on a single child
    sheet — a global net flagged on several sheets would read as multiple
    power sources and fail ERC.

    Returns the written path. Fails closed (via :class:`UnresolvedMPNError`)
    if any component MPN can't be resolved. Components whose KiCad symbol
    can't be located in any reachable library are skipped rather than
    emitted as an invalid ``lib_symbols`` reference.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resolved = resolve_components(board.components)
    sym_index = symbol_index if symbol_index is not None else default_index()
    # lib_symbols defs and pin coordinates both come from the same place.
    search_paths = sym_index.search_paths
    sch_uuid = _ns(f"{board.name}:schematic")

    # Which net (if any) each (refdes, pin) belongs to.
    pin_net: dict[tuple[str, str], str] = {}
    for net in board.nets:
        for member in net.members:
            refdes, _, pin = member.partition(".")
            pin_net[(refdes, pin)] = net.name

    # Every embedded lib-symbol definition, keyed by its effective lib_id.
    lib_defs: dict[str, str] = {}

    def _resolve_symbol(lib_id: str) -> str | None:
        """Resolve a lib_id (following ``extends``) and stage its definition."""
        result = extract_lib_symbol(lib_id, search_paths)
        if result is None:
            return None
        effective_id, block = result
        lib_defs.setdefault(effective_id, block)
        return effective_id

    # Place components in a single wide row. A symbol whose library can't
    # be found is skipped — an unresolved lib_id breaks the whole file.
    symbols: list[_PlacedSymbol] = []
    column = 0
    for r in resolved:
        effective_id = _resolve_symbol(r.symbol)
        if effective_id is None:
            continue
        symbols.append(_PlacedSymbol(
            lib_id=effective_id,
            refdes=r.refdes,
            value=r.value or r.mpn,
            footprint=r.footprint,
            mpn=r.mpn,
            in_bom=True,
            x=_snap(_COMPONENT_ROW_X0_MM + column * _COMPONENT_PITCH_MM),
            y=_snap(_COMPONENT_ROW_Y_MM),
            pin_map=sym_index.pin_positions(effective_id) or {},
        ))
        column += 1

    # A PWR_FLAG per power/ground net so ERC's "power input pin not driven"
    # check is satisfied. The flag's pin carries a label of the net name.
    if power_flag_nets is None:
        flag_net_names = undriven_power_net_names(board, symbol_index=symbol_index)
    else:
        flag_net_names = list(power_flag_nets)

    flag_pin_targets: list[tuple[str, float, float]] = []  # (net, x, y)
    flag_id = _resolve_symbol(_PWR_FLAG_LIB_ID)
    if flag_id is not None:
        flag_pins = sym_index.pin_positions(flag_id) or {}
        for flag_index, net_name in enumerate(flag_net_names):
            fx = _snap(_FLAG_ROW_X0_MM + flag_index * _FLAG_PITCH_MM)
            fy = _snap(_FLAG_ROW_Y_MM)
            symbols.append(_PlacedSymbol(
                lib_id=flag_id,
                refdes=f"#FLG{flag_index + 1}",
                value="PWR_FLAG",
                footprint="",
                mpn="",
                in_bom=False,
                x=fx,
                y=fy,
                pin_map=flag_pins,
                is_power_flag=True,
            ))
            for _pin_number, (px, py) in flag_pins.items():
                flag_pin_targets.append((net_name, fx + px, fy - py))

    # ----- assemble the file -------------------------------------------
    lines: list[str] = [
        "(kicad_sch",
        f"\t(version {_SCH_FORMAT_VERSION})",
        '\t(generator "ki-mcp-pcb")',
        '\t(generator_version "9.0")',
        f"\t(uuid {_quote(sch_uuid)})",
        '\t(paper "A4")',
        "\t(lib_symbols",
    ]
    for block in lib_defs.values():
        lines.append("\t\t" + block)
    lines.append("\t)")

    for sym in symbols:
        lines.append(_emit_symbol(sym, board.name, sch_uuid))

    # Connectivity for component pins: a label on each net-bearing pin,
    # a no-connect flag on the rest. PWR_FLAG pins (synthetic, no refdes
    # in pin_net) are handled separately below.
    for sym in symbols:
        if sym.is_power_flag:
            continue
        for pin_number, (px, py) in sorted(sym.pin_map.items()):
            label_x = sym.x + px
            label_y = sym.y - py
            pin_net_name = pin_net.get((sym.refdes, pin_number))
            seed = f"{sym.refdes}.{pin_number}"
            if pin_net_name is not None:
                lines.append(_emit_label(pin_net_name, label_x, label_y, seed))
            else:
                lines.append(_emit_no_connect(label_x, label_y, seed))

    # PWR_FLAG pins carry a label of the net they assert.
    for net_name, px, py in flag_pin_targets:
        lines.append(_emit_label(net_name, px, py, f"pwrflag:{net_name}"))

    lines += [
        "\t(sheet_instances",
        '\t\t(path "/"',
        '\t\t\t(page "1")',
        "\t\t)",
        "\t)",
        "\t(embedded_fonts no)",
        ")",
    ]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
