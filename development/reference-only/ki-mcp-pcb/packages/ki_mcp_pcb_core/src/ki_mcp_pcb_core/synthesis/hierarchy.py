"""Hierarchical sheet emission for big boards.

When a board has many components or multiple partitions, a flat schematic
becomes unreadable. KiCad's answer is hierarchical sheets: a *parent*
``.kicad_sch`` contains a ``Sheet`` block per logical group, each pointing
at a *child* ``.kicad_sch`` that contains the actual symbols.

This module's job:

  1. Decide whether a board warrants hierarchy (count threshold or
     multiple non-trivial partitions).
  2. Split the board's components + relevant nets into groups (one per
     ``synthesis.sch_layout`` cluster).
  3. Emit child schematics with their slice of the netlist.
  4. Emit a parent schematic with ``HierarchicalSheet`` placeholders.

Cross-sheet connectivity is still expressed via global labels — KiCad
treats global labels as electrically connected across all sheets in the
project.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board, Component, Net

# Heuristic threshold. KiCad's eeschema starts feeling cramped past
# 25-30 symbols on one sheet; multi-partition boards benefit from
# splitting even at smaller counts.
_HIERARCHY_THRESHOLD_COMPONENTS = 30


@dataclass(frozen=True)
class SheetSlice:
    """Components + nets that belong to a single child sheet."""

    sheet_name: str       # human-readable, e.g. "analog" or cluster anchor
    file_name: str        # child filename (``<board>__<slug>.kicad_sch``)
    components: list[Component]
    nets: list[Net]


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def needs_hierarchy(board: Board) -> bool:
    """``True`` if the board should be split into hierarchical sheets.

    Trigger conditions (any one):
      * Component count exceeds the threshold (currently 30).
      * Board declares ≥2 non-trivial partitions among its components.
    """
    if len(board.components) > _HIERARCHY_THRESHOLD_COMPONENTS:
        return True
    partitions = {c.partition for c in board.components if c.partition is not None}
    return len(partitions) >= 2


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def slice_board(board: Board) -> list[SheetSlice]:
    """Split the board into one ``SheetSlice`` per cluster.

    Uses :func:`ki_mcp_pcb_core.synthesis.sch_layout.cluster_components`
    so the visual grouping matches the design intent. A net is included
    in a sheet's slice when it has at least one member whose refdes is
    in that sheet. Nets that span multiple sheets appear in every sheet
    they touch — KiCad's global labels merge them at the parent level.
    """
    from ki_mcp_pcb_core.synthesis.sch_layout import (
        assign_decouplers,
        cluster_components,
    )

    clusters = cluster_components(board)
    decouplers = assign_decouplers(board)

    # Apply the same decoupler-fold as the layout layer so caps sit with
    # their parent IC's sheet, not their own.
    merged: dict[str, list[str]] = {a: list(m) for a, m in clusters.items()}

    def find_cluster(refdes: str) -> str | None:
        for anc, members in merged.items():
            if refdes in members:
                return anc
        return None

    for cap, parent_ic in decouplers.items():
        cap_anchor = find_cluster(cap)
        if cap_anchor is not None:
            if len(merged[cap_anchor]) == 1 and merged[cap_anchor][0] == cap:
                merged.pop(cap_anchor, None)
            else:
                merged[cap_anchor].remove(cap)
        ic_anchor = find_cluster(parent_ic)
        if ic_anchor is not None and cap not in merged[ic_anchor]:
            merged[ic_anchor].append(cap)
        elif ic_anchor is None:
            merged[cap] = [cap]

    comp_by_refdes = {c.refdes: c for c in board.components}

    slices: list[SheetSlice] = []
    for anchor in sorted(merged):
        members = merged[anchor]
        member_set = set(members)
        components = [comp_by_refdes[r] for r in members if r in comp_by_refdes]
        nets = [
            n for n in board.nets
            if any(m.split(".", 1)[0] in member_set for m in n.members)
        ]
        sheet_name = _slug_for(anchor, components)
        slices.append(SheetSlice(
            sheet_name=sheet_name,
            file_name=f"{board.name}__{sheet_name}.kicad_sch",
            components=components,
            nets=nets,
        ))
    return slices


def _slug_for(anchor: str, components: list[Component]) -> str:
    """Sheet name: partition if available, otherwise anchor refdes lower-cased."""
    partitions = {c.partition for c in components if c.partition}
    if partitions:
        # Prefer a meaningful partition name; if multiple, hyphenate alphabetical.
        return "-".join(sorted(p for p in partitions if p is not None))
    return anchor.lower()


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def write_hierarchical(
    board: Board,
    out_path: Path,
    *,
    symbol_index: object | None = None,
) -> list[Path]:
    """Emit a parent ``.kicad_sch`` plus one child per slice.

    Returns the list of files written, parent first.
    """
    from ki_mcp_pcb_core.synthesis.schematic import (
        undriven_power_net_names,
        write_schematic,
    )

    out_path = Path(out_path)
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    slices = slice_board(board)
    written: list[Path] = []

    # Each project-wide power net that needs a PWR_FLAG is flagged once,
    # on the first child only. The labels are global, so one flag drives
    # the net across all sheets; flagging it on several would read as
    # multiple power sources and fail ERC.
    power_net_names = undriven_power_net_names(board)

    # Emit each child as a regular flat schematic over its subset of
    # the board (cloned with the slice's components + nets only).
    for index, sl in enumerate(slices):
        child_board = board.model_copy(
            update={
                "name": f"{board.name}__{sl.sheet_name}",
                "components": sl.components,
                "nets": sl.nets,
            },
            deep=True,
        )
        child_path = out_dir / sl.file_name
        written.append(write_schematic(
            child_board,
            child_path,
            symbol_index=symbol_index,  # type: ignore[arg-type]
            power_flag_nets=power_net_names if index == 0 else [],
        ))

    # Emit the parent with Sheet placeholders pointing at each child.
    parent_path = _write_parent(board, out_path, slices)
    return [parent_path, *written]


def _write_parent(board: Board, parent_path: Path, slices: list[SheetSlice]) -> Path:
    """Emit the KiCad-10 parent sheet referencing each child by filename.

    Cross-sheet connectivity needs no hierarchical sheet pins here: the
    child schematics use *global* labels (see ``schematic.write_schematic``),
    which KiCad treats as connected project-wide. The parent therefore
    only has to be a valid ``.kicad_sch`` carrying one ``(sheet …)`` block
    per child.
    """
    from ki_mcp_pcb_core.synthesis.schematic import (
        _SCH_FORMAT_VERSION,
        _fmt,
        _ns,
        _quote,
    )

    parent_uuid = _ns(f"{board.name}:hierarchy-parent")
    lines: list[str] = [
        "(kicad_sch",
        f"\t(version {_SCH_FORMAT_VERSION})",
        '\t(generator "ki-mcp-pcb")',
        '\t(generator_version "9.0")',
        f"\t(uuid {_quote(parent_uuid)})",
        '\t(paper "A4")',
        "\t(lib_symbols)",
    ]
    sheet_w, sheet_h = 30.0, 20.0
    for i, sl in enumerate(slices):
        x = 30.0 + (i % 4) * 50.0
        y = 30.0 + (i // 4) * 45.0
        lines += [
            "\t(sheet",
            f"\t\t(at {_fmt(x)} {_fmt(y)})",
            f"\t\t(size {_fmt(sheet_w)} {_fmt(sheet_h)})",
            "\t\t(exclude_from_sim no)",
            "\t\t(in_bom yes)",
            "\t\t(on_board yes)",
            "\t\t(dnp no)",
            "\t\t(fields_autoplaced yes)",
            "\t\t(stroke",
            "\t\t\t(width 0.1524)",
            "\t\t\t(type solid)",
            "\t\t)",
            "\t\t(fill",
            "\t\t\t(color 0 0 0 0.0)",
            "\t\t)",
            f"\t\t(uuid {_quote(_ns(f'sheet:{sl.sheet_name}'))})",
            f'\t\t(property "Sheetname" {_quote(sl.sheet_name)}',
            f"\t\t\t(at {_fmt(x)} {_fmt(y - 0.7112)} 0)",
            "\t\t\t(effects",
            "\t\t\t\t(font",
            "\t\t\t\t\t(size 1.27 1.27)",
            "\t\t\t\t)",
            "\t\t\t\t(justify left bottom)",
            "\t\t\t)",
            "\t\t)",
            f'\t\t(property "Sheetfile" {_quote(sl.file_name)}',
            f"\t\t\t(at {_fmt(x)} {_fmt(y + sheet_h + 0.7112)} 0)",
            "\t\t\t(effects",
            "\t\t\t\t(font",
            "\t\t\t\t\t(size 1.27 1.27)",
            "\t\t\t\t)",
            "\t\t\t\t(justify left top)",
            "\t\t\t)",
            "\t\t)",
            "\t\t(instances",
            f"\t\t\t(project {_quote(board.name)}",
            f'\t\t\t\t(path "/{parent_uuid}"',
            f'\t\t\t\t\t(page "{i + 2}")',
            "\t\t\t\t)",
            "\t\t\t)",
            "\t\t)",
            "\t)",
        ]
    lines += [
        "\t(sheet_instances",
        '\t\t(path "/"',
        '\t\t\t(page "1")',
        "\t\t)",
        "\t)",
        "\t(embedded_fonts no)",
        ")",
    ]
    parent_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parent_path


__all__ = [
    "SheetSlice",
    "needs_hierarchy",
    "slice_board",
    "write_hierarchical",
]
