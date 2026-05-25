"""`ui_stackup_set` — UI-only stackup mutation (M3-T-01).

The Claude-facing surface for stackup edits goes through declarative
hints (`kc_stackup_propose`, future work). The PCB editor's
`StackupEditor` panel mutates the in-memory project's `project["stackup"]`
block directly through the gateway, mirroring the
[`ui_netclass_set`][ui_netclass_set] pattern:

- panel reads the current stackup from `projectStore`,
- user edits layers locally (insert / delete / reorder / change
  thickness / Er / loss-tangent / material),
- on Save, the panel POSTs the whole payload — `ui_stackup_set`
  validates invariants and replaces `project["stackup"]` atomically.

Why whole-payload-on-save rather than per-row mutations:

- KiCad's stack manager edits this way (the dialog is modal — you
  commit a whole stackup at once).
- Layer ordering carries invariants ("F.Cu is first copper, B.Cu is
  last copper, dielectric between any two copper layers"). Validating
  those after each keystroke would be friction; validating once on
  Save is correct.
- The stackup is small (typical board: 4-8 layers including
  dielectric; rarely more than 16). The full payload is a few hundred
  bytes — sending it whole is cheaper than orchestrating 6 round-trips
  for an 8-layer edit.

[ui_netclass_set]: ./netclass_edit.py
"""

from __future__ import annotations

from typing import Any

# Allowed `kind` values match the KCIR `StackupLayerKind` enum (Rust
# `crates/ki/src/kcir/stackup.rs`) serialised as snake_case.
ALLOWED_KINDS = frozenset({
    "copper",
    "dielectric",
    "soldermask",
    "silkscreen",
    "paste",
    "adhesive",
})


def _validate_layer(idx: int, layer: dict[str, Any]) -> str | None:
    """Return an error string if `layer` is malformed, else None."""
    name = layer.get("name")
    if not isinstance(name, str) or not name.strip():
        return f"layer #{idx}: `name` is required"
    kind = layer.get("kind")
    if kind not in ALLOWED_KINDS:
        return (
            f"layer #{idx} ({name!r}): `kind` must be one of "
            f"{sorted(ALLOWED_KINDS)}, got {kind!r}"
        )
    try:
        thickness = float(layer.get("thickness_mm", 0.0))
    except (TypeError, ValueError):
        return f"layer #{idx} ({name!r}): `thickness_mm` must be a number"
    if thickness < 0:
        return f"layer #{idx} ({name!r}): `thickness_mm` must be ≥ 0"
    for opt_key in ("dielectric_constant", "loss_tangent"):
        raw = layer.get(opt_key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return f"layer #{idx} ({name!r}): `{opt_key}` must be a number or null"
        if value < 0:
            return f"layer #{idx} ({name!r}): `{opt_key}` must be ≥ 0"
    return None


def _validate_anchors(layers: list[dict[str, Any]]) -> str | None:
    """`F.Cu` must be the first copper layer and `B.Cu` the last —
    matches KiCad's stack-manager invariant."""
    coppers = [layer for layer in layers if layer.get("kind") == "copper"]
    if not coppers:
        # An all-dielectric "panel" is allowed for substrate-only
        # workflows but the more common case is at least F.Cu.
        return None
    first = coppers[0].get("name")
    last = coppers[-1].get("name")
    if first != "F.Cu":
        return f"first copper layer must be `F.Cu`, got {first!r}"
    if last != "B.Cu":
        return f"last copper layer must be `B.Cu`, got {last!r}"
    return None


def _normalise_layer(layer: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `layer` with the canonical KCIR field set."""
    out: dict[str, Any] = {
        "name": str(layer["name"]).strip(),
        "kind": str(layer["kind"]),
        "thickness_mm": float(layer.get("thickness_mm", 0.0)),
        "dielectric_constant": (
            float(layer["dielectric_constant"])
            if layer.get("dielectric_constant") is not None
            else None
        ),
        "loss_tangent": (
            float(layer["loss_tangent"])
            if layer.get("loss_tangent") is not None
            else None
        ),
        "color": str(layer.get("color") or ""),
    }
    return out


def ui_stackup_set(
    project: dict[str, Any],
    *,
    layers: list[dict[str, Any]] | None = None,
    finish: str | None = None,
    controlled_impedance: bool | None = None,
    power_plane_layers: list[str] | None = None,
) -> dict[str, Any]:
    """Atomically replace `project["stackup"]` with the given fields.

    Validates per-layer required fields, the `F.Cu`/`B.Cu` anchor
    invariant, and that layer names are unique. Recomputes
    `board_thickness_mm` from the layer thicknesses (KiCad doesn't
    persist it as an independent field — it's always the sum).

    Returns `{"ok": True, "stackup": <new stackup dict>}` on success
    or `{"ok": False, "error": <message>}` on validation failure.
    """
    if layers is None:
        return {"ok": False, "error": "`layers` is required"}
    if not isinstance(layers, list):
        return {"ok": False, "error": "`layers` must be a list"}

    # Per-layer validation.
    for idx, layer in enumerate(layers):
        if not isinstance(layer, dict):
            return {
                "ok": False,
                "error": f"layer #{idx}: expected an object, got {type(layer).__name__}",
            }
        err = _validate_layer(idx, layer)
        if err is not None:
            return {"ok": False, "error": err}

    # Uniqueness.
    seen_names: set[str] = set()
    for layer in layers:
        name = str(layer["name"]).strip()
        if name in seen_names:
            return {"ok": False, "error": f"duplicate layer name: {name!r}"}
        seen_names.add(name)

    # Anchor invariant.
    anchor_err = _validate_anchors(layers)
    if anchor_err is not None:
        return {"ok": False, "error": anchor_err}

    normalised = [_normalise_layer(layer) for layer in layers]
    board_thickness = sum(layer["thickness_mm"] for layer in normalised)

    current = project.get("stackup") or {}
    new_stackup: dict[str, Any] = {
        "layers": normalised,
        "power_plane_layers": (
            [str(n) for n in power_plane_layers]
            if power_plane_layers is not None
            else list(current.get("power_plane_layers") or [])
        ),
        "controlled_impedance": (
            bool(controlled_impedance)
            if controlled_impedance is not None
            else bool(current.get("controlled_impedance", False))
        ),
        "board_thickness_mm": board_thickness,
        "finish": (
            str(finish)
            if finish is not None
            else str(current.get("finish") or "")
        ),
    }
    project["stackup"] = new_stackup
    return {
        "ok": True,
        "stackup": new_stackup,
        "project": project,
    }


__all__ = ["ALLOWED_KINDS", "ui_stackup_set"]
