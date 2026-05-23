"""Freerouting subprocess wrapper (M2-P-06).

The round-trip is:

1. `kicad-cli pcb export dsn <pcb> -o <pcb>.dsn` — export the board
   description Freerouting understands.
2. `java -jar <freerouting.jar> -de <pcb>.dsn -do <pcb>.ses -mp <passes>` —
   run Freerouting headless.
3. `kicad-cli pcb import ses <pcb> --ses <pcb>.ses` — import the
   routed SES back into the `.kicad_pcb`.

Freerouting is GPL-2.0 and ships as a `.jar`; we invoke it via
subprocess only (never JNI) so kiclaude's MIT/Apache licensing is
not contaminated (spec NFR-009).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 10 minutes — typical 4-layer board completes in 2-4 min on a single
# pass; bigger boards or higher pass counts take longer.
DEFAULT_TIMEOUT_S = 600.0

# How many Freerouting auto-route passes to run by default. The spec
# (FR-027) leaves this configurable; a single pass is enough to
# verify the DSN/SES round-trip, more passes refine the topology.
DEFAULT_PASSES = 1


@dataclass(slots=True)
class FreeroutingResult:
    """Outcome of a DSN → Freerouting → SES round-trip."""

    ok: bool
    pcb_path: str
    dsn_path: str | None = None
    ses_path: str | None = None
    log: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "pcb_path": self.pcb_path,
            "dsn_path": self.dsn_path,
            "ses_path": self.ses_path,
            "log": self.log,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
        }


async def run_freerouting(
    pcb_path: str | Path,
    *,
    freerouting_jar: str | None = None,
    passes: int = DEFAULT_PASSES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    kicad_cli_binary: str = "kicad-cli",
    java_binary: str = "java",
) -> FreeroutingResult:
    """Run the full DSN/SES round-trip and return the artifacts.

    `freerouting_jar` defaults to `KICLAUDE_FREEROUTING_JAR` from env.
    Returns `FreeroutingResult.ok=False` whenever any subprocess hop
    fails, with the diagnostic in `error`.
    """
    started = asyncio.get_event_loop().time()
    target = Path(pcb_path).expanduser()
    if not target.is_absolute():
        target = target.resolve()
    if not target.exists():
        return _err(str(target), f"PCB not found: {pcb_path}", _duration(started))
    if target.suffix != ".kicad_pcb":
        return _err(
            str(target),
            f"target must be a .kicad_pcb, got {target.suffix or 'no extension'}",
            _duration(started),
        )

    jar_path = freerouting_jar or os.environ.get("KICLAUDE_FREEROUTING_JAR", "")
    if not jar_path:
        return _err(
            str(target),
            "Freerouting jar path missing (set KICLAUDE_FREEROUTING_JAR)",
            _duration(started),
        )
    if not Path(jar_path).exists():
        return _err(
            str(target),
            f"Freerouting jar not found at {jar_path}",
            _duration(started),
        )
    kicad_cli = shutil.which(kicad_cli_binary)
    if kicad_cli is None:
        return _err(
            str(target),
            f"{kicad_cli_binary} not on PATH",
            _duration(started),
        )
    java = shutil.which(java_binary)
    if java is None:
        return _err(
            str(target),
            f"{java_binary} not on PATH (required to run Freerouting)",
            _duration(started),
        )

    log: list[str] = []
    dsn = target.with_suffix(".dsn")
    ses = target.with_suffix(".ses")

    # 1. Export DSN.
    rc, msg = await _spawn(
        [kicad_cli, "pcb", "export", "dsn", "--output", str(dsn), str(target)],
        timeout_s=timeout_s,
        label="dsn",
        log=log,
    )
    if rc != 0:
        return _err(
            str(target),
            f"DSN export failed: {msg}",
            _duration(started),
            dsn_path=str(dsn) if dsn.exists() else None,
            log=log,
            exit_code=rc,
        )

    # 2. Run Freerouting.
    rc, msg = await _spawn(
        [
            java,
            "-jar",
            jar_path,
            "-de",
            str(dsn),
            "-do",
            str(ses),
            "-mp",
            str(max(1, int(passes))),
        ],
        timeout_s=timeout_s,
        label="freerouting",
        log=log,
    )
    if rc != 0:
        return _err(
            str(target),
            f"Freerouting failed: {msg}",
            _duration(started),
            dsn_path=str(dsn),
            ses_path=str(ses) if ses.exists() else None,
            log=log,
            exit_code=rc,
        )
    if not ses.exists():
        return _err(
            str(target),
            "Freerouting reported success but no .ses produced",
            _duration(started),
            dsn_path=str(dsn),
            log=log,
        )

    # 3. Import SES back into the PCB.
    rc, msg = await _spawn(
        [
            kicad_cli,
            "pcb",
            "import",
            "ses",
            "--ses",
            str(ses),
            str(target),
        ],
        timeout_s=timeout_s,
        label="ses-import",
        log=log,
    )
    if rc != 0:
        return _err(
            str(target),
            f"SES import failed: {msg}",
            _duration(started),
            dsn_path=str(dsn),
            ses_path=str(ses),
            log=log,
            exit_code=rc,
        )

    return FreeroutingResult(
        ok=True,
        pcb_path=str(target),
        dsn_path=str(dsn),
        ses_path=str(ses),
        log=log,
        error=None,
        duration_ms=_duration(started),
        exit_code=0,
    )


async def _spawn(
    cmd: Iterable[str],
    *,
    timeout_s: float,
    label: str,
    log: list[str],
) -> tuple[int, str]:
    log.append(f"[{label}] $ {' '.join(cmd)}")
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
        return (-1, f"{label} timed out after {timeout_s}s")
    except (FileNotFoundError, PermissionError, OSError) as e:
        return (-1, f"{label} failed to launch: {e}")
    rc = proc.returncode if proc.returncode is not None else -1
    out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if out_text:
        log.append(f"[{label}] stdout: {out_text[:500]}")
    if err_text:
        log.append(f"[{label}] stderr: {err_text[:500]}")
    log.append(f"[{label}] exit {rc}")
    msg = err_text or out_text or f"exit {rc}"
    return (rc, msg)


def _err(
    pcb_path: str,
    message: str,
    duration_ms: int,
    *,
    dsn_path: str | None = None,
    ses_path: str | None = None,
    log: list[str] | None = None,
    exit_code: int | None = None,
) -> FreeroutingResult:
    return FreeroutingResult(
        ok=False,
        pcb_path=pcb_path,
        dsn_path=dsn_path,
        ses_path=ses_path,
        log=log or [],
        error=message,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


def _duration(started: float) -> int:
    delta = asyncio.get_event_loop().time() - started
    return max(0, int(delta * 1000.0))


__all__ = [
    "DEFAULT_PASSES",
    "DEFAULT_TIMEOUT_S",
    "FreeroutingResult",
    "run_freerouting",
]
