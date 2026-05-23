"""Routing: turn a placed board into a routed board.

Default: Freerouting headless via its JAR. Alternatives in M2+ include
KiCad 9's native autoroute through the IPC API and explicit human
sign-off for declared high-speed nets.

Freerouting is invoked as:

    java -jar freerouting.jar -de board.dsn -do board.ses

The PCB must first be exported to Specctra DSN via ``kicad-cli pcb
export dsn`` and then re-imported from the Freerouting SES output.
This module wraps that round-trip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ki_mcp_pcb_core._kicad_cli import run_kicad_cli

Router = Literal["freerouting", "kicad_native", "manual"]


# ---------------------------------------------------------------------------
# Errors and discovery
# ---------------------------------------------------------------------------


class RouterError(RuntimeError):
    """Routing failed or the router binary isn't available."""


class FreeroutingNotFoundError(RouterError):
    """Couldn't locate the Freerouting JAR or Java runtime."""


def _find_java() -> str:
    java = os.environ.get("JAVA") or shutil.which("java")
    if not java:
        raise FreeroutingNotFoundError(
            "java not found on PATH. Install a JRE (17+) and retry, or set "
            "the JAVA environment variable."
        )
    return java


def _find_freerouting_jar() -> str:
    jar = os.environ.get("FREEROUTING_JAR")
    if jar and Path(jar).exists():
        return jar
    raise FreeroutingNotFoundError(
        "FREEROUTING_JAR not set. Download freerouting.jar from "
        "https://github.com/freerouting/freerouting/releases and set the "
        "FREEROUTING_JAR environment variable. See `kimp doctor`."
    )


# ---------------------------------------------------------------------------
# Subprocess shim — tests swap this out
# ---------------------------------------------------------------------------


Runner = Callable[[Sequence[str]], "RouteRun"]


@dataclass(frozen=True)
class RouteRun:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def _real_runner(argv: Sequence[str]) -> RouteRun:
    proc = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
    )
    return RouteRun(
        args=list(argv), returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )


_runner: Runner = _real_runner


def set_runner_for_tests(runner: Runner | None) -> Runner:
    global _runner
    prev = _runner
    _runner = runner or _real_runner
    return prev


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteResult:
    pcb_path: Path
    dsn_path: Path
    ses_path: Path
    router: Router
    stdout: str


def route(
    pcb_path: Path,
    *,
    router: Router = "freerouting",
    out_dir: Path | None = None,
) -> RouteResult:
    """Route a placed PCB. Returns paths to the resulting artifacts.

    The board file is updated in place — Freerouting's SES output is
    re-imported via ``kicad-cli pcb import ses`` (handled by KiCad's
    internal tooling; we just orchestrate).
    """
    if router != "freerouting":
        raise NotImplementedError(
            f"Router {router!r} lands in M2+. Use 'freerouting' (default)."
        )

    pcb_path = Path(pcb_path)
    if not pcb_path.exists():
        raise FileNotFoundError(pcb_path)
    out_dir = Path(out_dir) if out_dir else pcb_path.parent

    dsn_path = out_dir / f"{pcb_path.stem}.dsn"
    ses_path = out_dir / f"{pcb_path.stem}.ses"

    # 1. KiCad: export DSN
    run_kicad_cli(["pcb", "export", "dsn", "--output", str(dsn_path), str(pcb_path)])

    # 2. Freerouting: route into SES
    java = _find_java()
    jar = _find_freerouting_jar()
    result = _runner([java, "-jar", jar, "-de", str(dsn_path), "-do", str(ses_path)])
    if result.returncode != 0:
        raise RouterError(
            f"Freerouting exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    # 3. KiCad: import SES back into the PCB
    run_kicad_cli(
        ["pcb", "import", "ses", "--input", str(ses_path), str(pcb_path)],
    )

    return RouteResult(
        pcb_path=pcb_path, dsn_path=dsn_path, ses_path=ses_path,
        router=router, stdout=result.stdout,
    )
