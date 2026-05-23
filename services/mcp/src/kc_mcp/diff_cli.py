"""`python -m kc_mcp.diff_cli <a.kicad_pcb> <b.kicad_pcb>` — M2-T-11
entry point.

Produces a structural delta between two `.kicad_pcb` files: which
footprints / tracks / vias / zones were added, removed, or modified.
Output defaults to a JSON document the M2-T-11 CLI prints; `--svg`
shells out to `pcbdraw` for a visual diff when the binary is on PATH.

Does NOT depend on `ki_native` — parses `.kicad_pcb` text directly via
a minimal Python S-expression tokenizer so the CLI works without the
PyO3 extension installed (the maturin step is optional for CLI use).

The structural diff focuses on **identity-bearing** fields (refdes,
uuid). Free-form properties (Reference position offsets,
silkscreen layout) are intentionally folded into a single per-section
"properties_changed" bucket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------
# Minimal S-expression parser. Enough to walk the canonical
# `.kicad_pcb` produced by `kiclaude_ki::format::v9::emit_pcb` (and
# the actual KiCad-9 IDE output). Returns nested Python lists where
# every list begins with the head symbol as a string.
# ---------------------------------------------------------------------


def parse_sexpr(text: str) -> list[Any]:
    """Tokenize + parse an S-expression document into nested lists."""
    pos = 0
    n = len(text)
    stack: list[list[Any]] = []
    out: list[Any] = []

    def skip_ws() -> int:
        nonlocal pos
        while pos < n:
            c = text[pos]
            if c.isspace():
                pos += 1
                continue
            # KiCad sometimes embeds `# …` line comments in fixtures.
            if c == "#":
                while pos < n and text[pos] != "\n":
                    pos += 1
                continue
            break
        return pos

    while pos < n:
        skip_ws()
        if pos >= n:
            break
        c = text[pos]
        if c == "(":
            pos += 1
            new: list[Any] = []
            if stack:
                stack[-1].append(new)
            else:
                out.append(new)
            stack.append(new)
        elif c == ")":
            pos += 1
            if not stack:
                raise ValueError(f"unmatched `)` at position {pos}")
            stack.pop()
        elif c == '"':
            start = pos + 1
            pos += 1
            buf: list[str] = []
            while pos < n:
                d = text[pos]
                if d == "\\":
                    if pos + 1 < n:
                        nxt = text[pos + 1]
                        buf.append(nxt)
                        pos += 2
                        continue
                if d == '"':
                    pos += 1
                    break
                buf.append(d)
                pos += 1
            else:
                raise ValueError(f"unterminated string starting at {start}")
            tok = "".join(buf)
            if stack:
                stack[-1].append(tok)
            else:
                out.append(tok)
        else:
            start = pos
            while pos < n and not text[pos].isspace() and text[pos] not in "()":
                pos += 1
            tok = text[start:pos]
            if stack:
                stack[-1].append(tok)
            else:
                out.append(tok)
    if stack:
        raise ValueError("unmatched `(` — missing closing parens")
    return out


def head(node: Any) -> str:
    """Return a list's head symbol (`""` for non-list / empty list)."""
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return ""


def find_child(node: list[Any], name: str) -> list[Any] | None:
    for c in node[1:]:
        if isinstance(c, list) and head(c) == name:
            return c
    return None


def find_children(node: list[Any], name: str) -> list[list[Any]]:
    return [c for c in node[1:] if isinstance(c, list) and head(c) == name]


def atom_or(node: list[Any] | None, default: str = "") -> str:
    if node is None:
        return default
    for c in node[1:]:
        if isinstance(c, str):
            return c
    return default


# ---------------------------------------------------------------------
# .kicad_pcb → identity-bearing entity tables.
# ---------------------------------------------------------------------


@dataclass(slots=True)
class PcbSummary:
    """Just enough shape from a `.kicad_pcb` to diff identity-bearing
    entities. Anything not in this dataclass is intentionally outside
    the diff scope."""

    footprints: list[dict[str, Any]] = field(default_factory=list)
    tracks: list[dict[str, Any]] = field(default_factory=list)
    vias: list[dict[str, Any]] = field(default_factory=list)
    zones: list[dict[str, Any]] = field(default_factory=list)
    nets: list[dict[str, Any]] = field(default_factory=list)


def parse_pcb(text: str) -> PcbSummary:
    docs = parse_sexpr(text)
    root: list[Any] | None = None
    for d in docs:
        if isinstance(d, list) and head(d) == "kicad_pcb":
            root = d
            break
    if root is None:
        raise ValueError("input is not a `(kicad_pcb …)` document")

    summary = PcbSummary()
    for fp in find_children(root, "footprint"):
        summary.footprints.append(_lift_footprint(fp))
    for seg in find_children(root, "segment"):
        summary.tracks.append(_lift_track(seg))
    for via in find_children(root, "via"):
        summary.vias.append(_lift_via(via))
    for zone in find_children(root, "zone"):
        summary.zones.append(_lift_zone(zone))
    for net in find_children(root, "net"):
        n = _lift_net(net)
        if n is not None:
            summary.nets.append(n)
    return summary


def _lift_footprint(node: list[Any]) -> dict[str, Any]:
    lib_id = ""
    for c in node[1:]:
        if isinstance(c, str):
            lib_id = c
            break
    refdes = ""
    value = ""
    mpn = ""
    for prop in find_children(node, "property"):
        body = [x for x in prop[1:] if isinstance(x, str)]
        if len(body) >= 2:
            key = body[0]
            val = body[1]
            if key == "Reference":
                refdes = val
            elif key == "Value":
                value = val
            elif key in {"MPN", "Manufacturer Part Number"}:
                mpn = val
    return {
        "uuid": atom_or(find_child(node, "uuid")),
        "lib_id": lib_id,
        "refdes": refdes,
        "value": value,
        "mpn": mpn,
        "layer": atom_or(find_child(node, "layer")),
        "position_mm": _read_at(node),
    }


def _lift_track(node: list[Any]) -> dict[str, Any]:
    start = _read_xy(find_child(node, "start"))
    end = _read_xy(find_child(node, "end"))
    width = _read_float(find_child(node, "width"))
    layer = atom_or(find_child(node, "layer"))
    net_id = _read_int(find_child(node, "net"))
    uuid_ = atom_or(find_child(node, "uuid"))
    locked = find_child(node, "locked") is not None
    return {
        "uuid": uuid_,
        "layer": layer,
        "net_id": net_id,
        "points_mm": [start, end],
        "width_mm": width,
        "locked": locked,
    }


def _lift_via(node: list[Any]) -> dict[str, Any]:
    return {
        "uuid": atom_or(find_child(node, "uuid")),
        "net_id": _read_int(find_child(node, "net")),
        "position_mm": _read_at(node),
        "drill_mm": _read_float(find_child(node, "drill")),
        "diameter_mm": _read_float(find_child(node, "size")),
    }


def _lift_zone(node: list[Any]) -> dict[str, Any]:
    return {
        "uuid": atom_or(find_child(node, "uuid")),
        "layer": atom_or(find_child(node, "layer")),
        "net_id": _read_int(find_child(node, "net")),
        "outline_points": _count_zone_points(node),
    }


def _lift_net(node: list[Any]) -> dict[str, Any] | None:
    body = [c for c in node[1:] if not isinstance(c, list)]
    if len(body) < 2:
        return None
    try:
        net_id = int(body[0])
    except (TypeError, ValueError):
        return None
    name = body[1] if isinstance(body[1], str) else ""
    return {"id": net_id, "name": name}


def _read_at(node: list[Any]) -> list[float]:
    at = find_child(node, "at")
    if at is None:
        return [0.0, 0.0]
    body = [x for x in at[1:] if isinstance(x, str)]
    xs = body[:2]
    while len(xs) < 2:
        xs.append("0")
    return [float(xs[0]), float(xs[1])]


def _read_xy(node: list[Any] | None) -> list[float]:
    if node is None:
        return [0.0, 0.0]
    body = [x for x in node[1:] if isinstance(x, str)]
    xs = body[:2]
    while len(xs) < 2:
        xs.append("0")
    return [float(xs[0]), float(xs[1])]


def _read_float(node: list[Any] | None) -> float:
    if node is None:
        return 0.0
    for c in node[1:]:
        if isinstance(c, str):
            try:
                return float(c)
            except ValueError:
                continue
    return 0.0


def _read_int(node: list[Any] | None) -> int:
    if node is None:
        return 0
    for c in node[1:]:
        if isinstance(c, str):
            try:
                return int(c)
            except ValueError:
                continue
    return 0


def _count_zone_points(node: list[Any]) -> int:
    poly = find_child(node, "polygon")
    if poly is None:
        return 0
    pts = find_child(poly, "pts")
    if pts is None:
        return 0
    return sum(1 for c in pts[1:] if isinstance(c, list) and head(c) == "xy")


# ---------------------------------------------------------------------
# Diff core.
# ---------------------------------------------------------------------


def diff_pcbs(before: PcbSummary, after: PcbSummary) -> dict[str, Any]:
    return {
        "footprints": _diff_by_key(
            before.footprints,
            after.footprints,
            key="uuid",
            fallback_key="refdes",
            compare_fields=("refdes", "value", "mpn", "layer", "position_mm", "lib_id"),
        ),
        "tracks": _diff_by_key(
            before.tracks,
            after.tracks,
            key="uuid",
            compare_fields=("layer", "net_id", "width_mm", "points_mm", "locked"),
        ),
        "vias": _diff_by_key(
            before.vias,
            after.vias,
            key="uuid",
            compare_fields=("net_id", "position_mm", "drill_mm", "diameter_mm"),
        ),
        "zones": _diff_by_key(
            before.zones,
            after.zones,
            key="uuid",
            compare_fields=("layer", "net_id", "outline_points"),
        ),
        "nets": _diff_by_key(
            before.nets,
            after.nets,
            key="id",
            compare_fields=("name",),
        ),
    }


def _diff_by_key(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    key: str,
    compare_fields: tuple[str, ...],
    fallback_key: str | None = None,
) -> dict[str, Any]:
    def make_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for it in items:
            k = str(it.get(key) or "")
            if not k and fallback_key:
                k = str(it.get(fallback_key) or "")
            if not k:
                continue
            out[k] = it
        return out

    by_before = make_index(before)
    by_after = make_index(after)
    added_keys = sorted(set(by_after) - set(by_before))
    removed_keys = sorted(set(by_before) - set(by_after))
    modified: list[dict[str, Any]] = []
    for k in sorted(set(by_before) & set(by_after)):
        diffs: dict[str, Any] = {}
        for f in compare_fields:
            if by_before[k].get(f) != by_after[k].get(f):
                diffs[f] = {"before": by_before[k].get(f), "after": by_after[k].get(f)}
        if diffs:
            modified.append({key: k, "changes": diffs})
    return {
        "added": [by_after[k] for k in added_keys],
        "removed": [by_before[k] for k in removed_keys],
        "modified": modified,
    }


# ---------------------------------------------------------------------
# SVG visual diff via pcbdraw (best-effort).
# ---------------------------------------------------------------------


async def render_svg_diff(
    a_path: Path, b_path: Path, out_path: Path, *, timeout_s: float = 60.0
) -> dict[str, Any]:
    """Render an SVG visual diff via pcbdraw if it's on PATH.

    Returns a dict with `ok`, `out_path`, `log`, and `error` so the
    caller can fold the result into the JSON envelope. pcbdraw missing
    is a soft failure — JSON diff remains the primary contract.
    """
    binary = shutil.which("pcbdraw")
    if binary is None:
        return {
            "ok": False,
            "out_path": str(out_path),
            "log": [],
            "error": "pcbdraw not on PATH — JSON diff is the primary contract",
        }
    cmd = [binary, "plot", "--diff", str(a_path), str(b_path), str(out_path)]
    log: list[str] = [f"$ {' '.join(cmd)}"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except TimeoutError:
        return {
            "ok": False,
            "out_path": str(out_path),
            "log": log,
            "error": f"pcbdraw timed out after {timeout_s}s",
        }
    except (FileNotFoundError, PermissionError, OSError) as e:
        return {
            "ok": False,
            "out_path": str(out_path),
            "log": log,
            "error": f"pcbdraw failed to launch: {e}",
        }
    rc = proc.returncode if proc.returncode is not None else -1
    out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if out_text:
        log.append(f"stdout: {out_text[:500]}")
    if err_text:
        log.append(f"stderr: {err_text[:500]}")
    if rc != 0 or not out_path.exists():
        return {
            "ok": False,
            "out_path": str(out_path),
            "log": log,
            "error": err_text or out_text or f"pcbdraw exited {rc}",
        }
    return {"ok": True, "out_path": str(out_path), "log": log, "error": None}


# ---------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kiclaude diff",
        description="Structural diff between two .kicad_pcb files.",
    )
    p.add_argument("before", help="First .kicad_pcb path.")
    p.add_argument("after", help="Second .kicad_pcb path.")
    p.add_argument(
        "--svg",
        default=None,
        help="Optional SVG output path. Requires pcbdraw on PATH.",
    )
    p.add_argument("--pr", action="store_true", help="PR-friendly compact output.")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI color in pr mode.")
    return p


def _pr_report(delta: dict[str, Any], *, color: bool) -> str:
    def paint(s: str, code: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if color else s

    lines: list[str] = ["kiclaude diff"]
    for section in ("footprints", "tracks", "vias", "zones", "nets"):
        d = delta.get(section) or {}
        added = len(d.get("added") or [])
        removed = len(d.get("removed") or [])
        modified = len(d.get("modified") or [])
        if added == 0 and removed == 0 and modified == 0:
            continue
        lines.append(
            f"  {section:11} "
            f"{paint('+' + str(added), '32') if added else '+0'}  "
            f"{paint('-' + str(removed), '31') if removed else '-0'}  "
            f"{paint('~' + str(modified), '33') if modified else '~0'}"
        )
    if len(lines) == 1:
        lines.append("  (no changes)")
    return "\n".join(lines)


async def _async_main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    a_path = Path(args.before).expanduser().resolve()
    b_path = Path(args.after).expanduser().resolve()
    if not a_path.exists():
        sys.stderr.write(f"kiclaude diff: {a_path} not found\n")
        return 2
    if not b_path.exists():
        sys.stderr.write(f"kiclaude diff: {b_path} not found\n")
        return 2
    try:
        before = parse_pcb(a_path.read_text(encoding="utf-8"))
        after = parse_pcb(b_path.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.stderr.write(f"kiclaude diff: parse error: {e}\n")
        return 2
    delta = diff_pcbs(before, after)

    svg_result: dict[str, Any] | None = None
    if args.svg:
        svg_result = await render_svg_diff(a_path, b_path, Path(args.svg).expanduser().resolve())

    payload = {
        "before": str(a_path),
        "after": str(b_path),
        "delta": delta,
        "svg": svg_result,
    }
    if args.pr:
        color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
        sys.stdout.write(_pr_report(delta, color=color) + "\n")
    else:
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    # Exit non-zero when there are any structural changes — that matches
    # the M2-T-11 "is this PR a no-op?" semantic.
    has_changes = any(
        (delta.get(s) or {}).get("added")
        or (delta.get(s) or {}).get("removed")
        or (delta.get(s) or {}).get("modified")
        for s in ("footprints", "tracks", "vias", "zones", "nets")
    )
    return 1 if has_changes else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PcbSummary", "diff_pcbs", "main", "parse_pcb", "parse_sexpr"]
