"""Fab export wrappers around `kicad-cli pcb export` (M2-P-02).

Three artifacts every house needs:

- **Gerbers** — one file per copper / mask / silk / paste / edge layer,
  produced by `kicad-cli pcb export gerbers --layers …`.
- **Excellon drill** — `.drl` files (plated + non-plated), produced by
  `kicad-cli pcb export drill --excellon-units mm`.
- **PnP (pick-and-place)** — CSV positions for SMD components,
  produced by `kicad-cli pcb export pos --units mm`.

Each helper returns an [`ExportArtifact`][ExportArtifact] listing every
generated file under a caller-supplied output directory. The wrapper
NEVER deletes the directory — the caller owns lifecycle (temp dir,
project staging, etc.).

JLCPCB naming conventions are spec-relevant (FR-030, FR-032). KiCad's
default filenames already match the JLC accept list once you set the
right plot suffixes; the helpers preserve those defaults and let the
caller post-rename if a fab has stricter rules.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 120s budget — plotting a dense 6-layer board takes longer than DRC.
DEFAULT_TIMEOUT_S = 120.0


# Default layer list KiCad uses when `--layers` is omitted is "all copper +
# soldermask + silkscreen + paste + Edge.Cuts". We pass it explicitly so
# the output is deterministic across kicad-cli versions.
DEFAULT_GERBER_LAYERS: tuple[str, ...] = (
    "F.Cu",
    "B.Cu",
    "F.Mask",
    "B.Mask",
    "F.SilkS",
    "B.SilkS",
    "F.Paste",
    "B.Paste",
    "Edge.Cuts",
)


@dataclass(slots=True)
class ExportArtifact:
    """A single export bundle (gerbers, drills, or pos) on disk."""

    ok: bool
    kind: str  # "gerbers" | "drill" | "pos"
    output_dir: str
    files: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "output_dir": self.output_dir,
            "files": self.files,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
        }


async def export_gerbers(
    pcb_path: str | Path,
    output_dir: str | Path,
    *,
    layers: Sequence[str] = DEFAULT_GERBER_LAYERS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
) -> ExportArtifact:
    """Run `kicad-cli pcb export gerbers` and return the produced
    files. Creates `output_dir` if missing.
    """
    target, out_dir, prep_err = _prep(pcb_path, output_dir)
    if prep_err is not None:
        return _err("gerbers", out_dir, prep_err, duration_ms=0)
    binary = shutil.which(kicad_cli_binary)
    if binary is None:
        return _err(
            "gerbers",
            out_dir,
            f"{kicad_cli_binary} not on PATH",
            duration_ms=0,
        )

    args = [
        "pcb",
        "export",
        "gerbers",
        "--output",
        out_dir,
        "--layers",
        ",".join(layers),
        target,
    ]
    return await _run("gerbers", binary, args, out_dir, timeout_s)


async def export_drill(
    pcb_path: str | Path,
    output_dir: str | Path,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
) -> ExportArtifact:
    """Run `kicad-cli pcb export drill` and return the `.drl` files.

    Uses millimeter units and the Excellon plated/non-plated split that
    JLC and OSHPark both accept by default.
    """
    target, out_dir, prep_err = _prep(pcb_path, output_dir)
    if prep_err is not None:
        return _err("drill", out_dir, prep_err, duration_ms=0)
    binary = shutil.which(kicad_cli_binary)
    if binary is None:
        return _err(
            "drill",
            out_dir,
            f"{kicad_cli_binary} not on PATH",
            duration_ms=0,
        )
    args = [
        "pcb",
        "export",
        "drill",
        "--output",
        out_dir,
        "--excellon-units",
        "mm",
        "--excellon-zeros-format",
        "suppressleading",
        "--separate-files",
        target,
    ]
    return await _run("drill", binary, args, out_dir, timeout_s)


async def export_pos(
    pcb_path: str | Path,
    output_dir: str | Path,
    *,
    side: str = "both",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
) -> ExportArtifact:
    """Run `kicad-cli pcb export pos` and return the CSV positions.

    `side` is one of `"front"`, `"back"`, or `"both"`. JLC's PnP service
    wants front+back combined; OSHPark wants per-side files.
    """
    target, out_dir, prep_err = _prep(pcb_path, output_dir)
    if prep_err is not None:
        return _err("pos", out_dir, prep_err, duration_ms=0)
    if side not in {"front", "back", "both"}:
        return _err(
            "pos",
            out_dir,
            f"side must be one of front/back/both, got {side!r}",
            duration_ms=0,
        )
    binary = shutil.which(kicad_cli_binary)
    if binary is None:
        return _err(
            "pos",
            out_dir,
            f"{kicad_cli_binary} not on PATH",
            duration_ms=0,
        )
    out_file = Path(out_dir) / f"{Path(target).stem}-pos.csv"
    args = [
        "pcb",
        "export",
        "pos",
        "--output",
        str(out_file),
        "--format",
        "csv",
        "--units",
        "mm",
        "--side",
        side,
        target,
    ]
    return await _run("pos", binary, args, out_dir, timeout_s)


# ---------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------


def _prep(
    pcb_path: str | Path,
    output_dir: str | Path,
) -> tuple[str, str, str | None]:
    """Resolve + validate paths. Returns `(target, out_dir, error)`.
    On failure, `target`/`out_dir` are best-effort strings so the
    diagnostic can still reference them in the response envelope.
    """
    target = Path(pcb_path).expanduser()
    if not target.is_absolute():
        target = target.resolve()
    if not target.exists():
        return (str(target), str(output_dir), f"PCB not found: {pcb_path}")
    if target.suffix != ".kicad_pcb":
        return (
            str(target),
            str(output_dir),
            f"target must be .kicad_pcb, got {target.suffix or 'no extension'}",
        )
    out = Path(output_dir).expanduser()
    if not out.is_absolute():
        out = out.resolve()
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return (str(target), str(out), f"cannot create output dir {out}: {e}")
    return (str(target), str(out), None)


async def _run(
    kind: str,
    binary: str,
    args: Iterable[str],
    out_dir: str,
    timeout_s: float,
) -> ExportArtifact:
    started = asyncio.get_event_loop().time()
    files_before = _snapshot(out_dir)
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except TimeoutError:
        return _err(
            kind,
            out_dir,
            f"kicad-cli timed out after {timeout_s}s",
            duration_ms=_duration(started),
        )
    except (FileNotFoundError, PermissionError, OSError) as e:
        return _err(
            kind,
            out_dir,
            f"kicad-cli failed to launch: {e}",
            duration_ms=_duration(started),
        )
    duration_ms = _duration(started)
    exit_code = proc.returncode if proc.returncode is not None else -1
    if exit_code != 0:
        stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        msg = stderr_text or stdout_text or f"kicad-cli exited {exit_code}"
        return _err(kind, out_dir, msg, duration_ms=duration_ms, exit_code=exit_code)
    # Produced files = the diff of the output directory before and
    # after the run. We list them as relative paths so the caller can
    # round-trip the artifact into a zip without leaking absolute paths.
    files_after = _snapshot(out_dir)
    new_files = sorted(files_after - files_before)
    return ExportArtifact(
        ok=True,
        kind=kind,
        output_dir=out_dir,
        files=new_files,
        error=None,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _snapshot(out_dir: str) -> set[str]:
    p = Path(out_dir)
    if not p.is_dir():
        return set()
    return {
        str(f.relative_to(p)) for f in p.rglob("*") if f.is_file()
    }


def _err(
    kind: str,
    out_dir: str,
    message: str,
    *,
    duration_ms: int,
    exit_code: int | None = None,
) -> ExportArtifact:
    return ExportArtifact(
        ok=False,
        kind=kind,
        output_dir=out_dir,
        files=[],
        error=message,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _duration(started: float) -> int:
    delta = asyncio.get_event_loop().time() - started
    return max(0, int(delta * 1000.0))


async def export_step(
    pcb_path: str | Path,
    output_dir: str | Path,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
    no_dnp: bool = True,
    no_unspecified: bool = False,
    subst_models: bool = True,
    board_only: bool = False,
) -> ExportArtifact:
    """Run `kicad-cli pcb export step` and return the produced
    `<board>.step` file (M3-P-09).

    Defaults align with how the M3-T-06 `kithree` viewer expects to
    consume the model: include components (so the 3D scene shows ICs
    + connectors), skip DNP parts (matches the BOM the assembler
    sees), and prefer STEP/IGS over VRML where both are present.
    Pass `board_only=True` for a bare board with no components.
    """
    target, out_dir, prep_err = _prep(pcb_path, output_dir)
    if prep_err is not None:
        return _err("step", out_dir, prep_err, duration_ms=0)
    binary = shutil.which(kicad_cli_binary)
    if binary is None:
        return _err(
            "step",
            out_dir,
            f"{kicad_cli_binary} not on PATH",
            duration_ms=0,
        )
    stem = Path(target).stem or "board"
    output_path = str(Path(out_dir) / f"{stem}.step")
    args: list[str] = [
        "pcb",
        "export",
        "step",
        "--output",
        output_path,
        "--force",
    ]
    if no_dnp:
        args.append("--no-dnp")
    if no_unspecified:
        args.append("--no-unspecified")
    if subst_models:
        args.append("--subst-models")
    if board_only:
        args.append("--board-only")
    args.append(target)
    return await _run("step", binary, args, out_dir, timeout_s)


__all__ = [
    "DEFAULT_GERBER_LAYERS",
    "DEFAULT_TIMEOUT_S",
    "ExportArtifact",
    "export_drill",
    "export_gerbers",
    "export_pos",
    "export_step",
]
