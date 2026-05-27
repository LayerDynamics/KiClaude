"""KiKit `panelize` wrapper (M2-P-07).

KiKit reads a JSON preset describing the panel layout — grid spacing,
mousebites, tab style, framing — and produces a single `.kicad_pcb`
representing the panel. The wrapper takes either an inline `config`
dict (serialized to a temp file) or a `preset_path` pointing at a
saved preset.

KiKit ships as a Python CLI; we invoke it via subprocess to keep the
subprocess sandbox uniform with kicad-cli + Freerouting.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 300.0


@dataclass(slots=True)
class PanelizeResult:
    ok: bool
    pcb_path: str
    output_path: str
    log: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "pcb_path": self.pcb_path,
            "output_path": self.output_path,
            "log": self.log,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
        }


async def run_panelize(
    pcb_path: str | Path,
    output_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    preset_path: str | Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kikit_binary: str = "kikit",
) -> PanelizeResult:
    started = asyncio.get_event_loop().time()
    target = Path(pcb_path).expanduser()
    if not target.is_absolute():
        target = target.resolve()
    out = Path(output_path).expanduser()
    if not out.is_absolute():
        out = out.resolve()
    if not target.exists():
        return _err(str(target), str(out), f"PCB not found: {pcb_path}", _duration(started))
    if target.suffix != ".kicad_pcb":
        return _err(
            str(target),
            str(out),
            f"target must be a .kicad_pcb, got {target.suffix or 'no extension'}",
            _duration(started),
        )
    # Validate the request (config/preset) before checking external-tool
    # availability, so a missing config is reported as such regardless of whether
    # kikit happens to be installed (CI runners have no kikit on PATH).
    if config is None and preset_path is None:
        return _err(
            str(target),
            str(out),
            "either `config` or `preset_path` must be supplied",
            _duration(started),
        )
    binary = shutil.which(kikit_binary)
    if binary is None:
        return _err(
            str(target),
            str(out),
            f"{kikit_binary} not on PATH",
            _duration(started),
        )
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _err(
            str(target),
            str(out),
            f"cannot create output dir {out.parent}: {e}",
            _duration(started),
        )

    log: list[str] = []
    preset_file: str | None = None
    cleanup: Path | None = None
    if config is not None:
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                delete=False,
                encoding="utf-8",
            )
            json.dump(config, tmp)
            tmp.flush()
            preset_file = tmp.name
            cleanup = Path(tmp.name)
            tmp.close()
        except OSError as e:
            return _err(
                str(target),
                str(out),
                f"failed to write inline kikit config: {e}",
                _duration(started),
            )
    elif preset_path:
        preset_file = str(preset_path)
        if not Path(preset_file).exists():
            return _err(
                str(target),
                str(out),
                f"preset file not found: {preset_file}",
                _duration(started),
            )

    cmd = [
        binary,
        "panelize",
        "--preset",
        str(preset_file),
        str(target),
        str(out),
    ]
    log.append(f"$ {' '.join(cmd)}")
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
        if cleanup is not None and cleanup.exists():
            cleanup.unlink(missing_ok=True)
        return _err(
            str(target),
            str(out),
            f"kikit timed out after {timeout_s}s",
            _duration(started),
        )
    except (FileNotFoundError, PermissionError, OSError) as e:
        if cleanup is not None and cleanup.exists():
            cleanup.unlink(missing_ok=True)
        return _err(
            str(target),
            str(out),
            f"kikit failed to launch: {e}",
            _duration(started),
        )
    if cleanup is not None and cleanup.exists():
        cleanup.unlink(missing_ok=True)
    rc = proc.returncode if proc.returncode is not None else -1
    stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if stdout_text:
        log.append(f"stdout: {stdout_text[:500]}")
    if stderr_text:
        log.append(f"stderr: {stderr_text[:500]}")
    if rc != 0:
        return _err(
            str(target),
            str(out),
            stderr_text or stdout_text or f"kikit exited {rc}",
            _duration(started),
            log=log,
            exit_code=rc,
        )
    return PanelizeResult(
        ok=True,
        pcb_path=str(target),
        output_path=str(out),
        log=log,
        error=None,
        duration_ms=_duration(started),
        exit_code=rc,
    )


def _err(
    pcb_path: str,
    output_path: str,
    message: str,
    duration_ms: int,
    *,
    log: list[str] | None = None,
    exit_code: int | None = None,
) -> PanelizeResult:
    return PanelizeResult(
        ok=False,
        pcb_path=pcb_path,
        output_path=output_path,
        log=log or [],
        error=message,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _duration(started: float) -> int:
    delta = asyncio.get_event_loop().time() - started
    return max(0, int(delta * 1000.0))


__all__ = ["DEFAULT_TIMEOUT_S", "PanelizeResult", "run_panelize"]
