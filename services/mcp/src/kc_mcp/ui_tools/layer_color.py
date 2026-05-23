"""`ui_layer_color_set` + `ui_layer_reorder` — per-layer view-settings
mutations for the M2-T-08 layer panel.

These persist the user's PCB layer customisations into the project
dict. The on-disk emitter writes them back to `.kicad_pro`'s
`board.layer_colors` block (and to the `pcb.layers` ordering for the
reorder side) so they survive a project reload.

The `Default` family of KiCad layers (`F.Cu`, `B.Cu`, `Edge.Cuts`,
`F.Mask`, `B.Mask`, `F.SilkS`, `B.SilkS`) has fixed STACKUP positions
in pcbnew — `F.Cu` is always layer 0 and `B.Cu` is always the
highest copper layer index. The reorder tool enforces this: only
inner copper layers (`In*.Cu`) and user layers can be moved, and
copper / non-copper layers can only swap with their own family.
"""

from __future__ import annotations

import re
from typing import Any

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_FIXED_TOP_BOTTOM = {"F.Cu", "B.Cu"}


def _layer_kind(layer: dict[str, Any]) -> str:
    """Look up a layer's KCIR kind, defaulting to `"user"` if absent."""
    kind = layer.get("kind")
    if isinstance(kind, str) and kind:
        return kind
    return "user"


def ui_layer_color_set(
    project: dict[str, Any],
    *,
    layer_id: int,
    color: str,
) -> dict[str, Any]:
    """Persist a per-layer colour into `pcb.layer_colors`.

    `color` is `#RRGGBB`. Stored alongside the layer's id so the
    emitter can write it back to `.kicad_pro` without re-deriving
    the lookup at emit time.
    """
    if not isinstance(layer_id, int):
        return {"ok": False, "error": "`layer_id` must be an int"}
    if not isinstance(color, str) or not _HEX_RE.match(color):
        return {
            "ok": False,
            "error": "`color` must be a `#RRGGBB` hex string",
        }
    pcb = project.setdefault("pcb", {})
    layers = pcb.get("layers") or []
    if not any(layer.get("id") == layer_id for layer in layers):
        return {
            "ok": False,
            "error": f"layer_id={layer_id} is not declared in pcb.layers",
        }
    layer_colors: dict[str, str] = pcb.setdefault("layer_colors", {})
    layer_colors[str(layer_id)] = color.lower()
    return {
        "ok": True,
        "layer_id": layer_id,
        "color": color.lower(),
        "project": project,
    }


def ui_layer_reorder(
    project: dict[str, Any],
    *,
    layer_id: int,
    target_id: int,
) -> dict[str, Any]:
    """Move `layer_id` to the slot currently held by `target_id` in
    `pcb.layers`, subject to physical-limit constraints:

    - `F.Cu` and `B.Cu` are fixed (always the first/last copper
      layer respectively) — moves involving them are refused.
    - A copper layer can only swap with another copper layer (a
      silkscreen layer can't be inserted between `F.Cu` and
      `In1.Cu`, for instance).
    - Same `kind` discipline applies to silkscreen/mask/paste/user.
    """
    if not isinstance(layer_id, int) or not isinstance(target_id, int):
        return {"ok": False, "error": "`layer_id` and `target_id` must be ints"}
    if layer_id == target_id:
        return {"ok": False, "error": "no-op reorder"}
    pcb = project.setdefault("pcb", {})
    layers: list[dict[str, Any]] = pcb.get("layers") or []
    from_idx = next(
        (i for i, layer in enumerate(layers) if layer.get("id") == layer_id),
        -1,
    )
    to_idx = next(
        (i for i, layer in enumerate(layers) if layer.get("id") == target_id),
        -1,
    )
    if from_idx < 0 or to_idx < 0:
        return {
            "ok": False,
            "error": "both layer_id and target_id must exist in pcb.layers",
        }
    src_layer = layers[from_idx]
    tgt_layer = layers[to_idx]
    src_name = src_layer.get("name", "")
    tgt_name = tgt_layer.get("name", "")
    if src_name in _FIXED_TOP_BOTTOM or tgt_name in _FIXED_TOP_BOTTOM:
        return {
            "ok": False,
            "error": (
                "F.Cu and B.Cu are fixed; cannot reorder across "
                "them"
            ),
        }
    if _layer_kind(src_layer) != _layer_kind(tgt_layer):
        return {
            "ok": False,
            "error": (
                f"cannot move a {_layer_kind(src_layer)!r} layer onto "
                f"a {_layer_kind(tgt_layer)!r} slot"
            ),
        }
    moved = layers.pop(from_idx)
    layers.insert(to_idx, moved)
    pcb["layers"] = layers
    return {
        "ok": True,
        "layer_id": layer_id,
        "moved_to": to_idx,
        "project": project,
    }


__all__ = ["ui_layer_color_set", "ui_layer_reorder"]
