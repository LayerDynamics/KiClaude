"""Shared kicad-cli subprocess runner.

All KiCad command-line invocations route through ``run_kicad_cli`` so we
have one place to:
  - locate the binary (``KICAD_CLI`` env override, ``which kicad-cli``)
  - normalize errors
  - parse the JSON report files kicad-cli emits

Tests inject a fake runner via ``set_runner_for_tests`` so we never
shell out in the unit suite.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class KiCadCLIError(RuntimeError):
    """kicad-cli failed or is not installed."""


class KiCadCLINotFoundError(KiCadCLIError):
    """kicad-cli binary couldn't be located on PATH or via KICAD_CLI env."""


# ---------------------------------------------------------------------------
# Runner protocol — tests swap this out
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompletedRun:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], CompletedRun]


def _real_runner(argv: Sequence[str]) -> CompletedRun:
    proc = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
    )
    return CompletedRun(
        args=list(argv), returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )


_runner: Runner = _real_runner


def set_runner_for_tests(runner: Runner | None) -> Runner:
    """Swap the runner. Returns the previous runner so tests can restore."""
    global _runner
    prev = _runner
    _runner = runner or _real_runner
    return prev


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


# Stock install locations probed when kicad-cli isn't on PATH. KiCad's
# macOS app bundle and Windows Program Files install never put the binary
# on PATH; newer versions are listed first so we prefer the latest install.
_DEFAULT_CLI_PATHS: tuple[str, ...] = (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/bin/kicad-cli",
    "/usr/local/bin/kicad-cli",
    "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe",
    "C:/Program Files/KiCad/9.0/bin/kicad-cli.exe",
    "C:/Program Files/KiCad/8.0/bin/kicad-cli.exe",
)


def find_kicad_cli() -> str:
    """Return the kicad-cli binary path, or raise ``KiCadCLINotFoundError``.

    Resolution order: ``KICAD_CLI`` env override → ``kicad-cli`` on PATH →
    the stock install locations in :data:`_DEFAULT_CLI_PATHS`.
    """
    override = os.environ.get("KICAD_CLI")
    if override:
        return override
    found = shutil.which("kicad-cli")
    if found:
        return found
    for candidate in _DEFAULT_CLI_PATHS:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise KiCadCLINotFoundError(
        "kicad-cli not found on PATH or in any stock install location. "
        "Install KiCad 9+ or set the KICAD_CLI environment variable to "
        "the binary path. See `kimp doctor`."
    )


def is_available() -> bool:
    try:
        find_kicad_cli()
    except KiCadCLINotFoundError:
        return False
    return True


# ---------------------------------------------------------------------------
# Run wrapper
# ---------------------------------------------------------------------------


def run_kicad_cli(args: Sequence[str], *, check: bool = True) -> CompletedRun:
    """Invoke kicad-cli with ``args``. Raises on non-zero exit when ``check``."""
    binary = find_kicad_cli()
    full = [binary, *args]
    result = _runner(full)
    if check and result.returncode != 0:
        raise KiCadCLIError(
            f"kicad-cli exited {result.returncode} for argv {full!r}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def read_kicad_json_report(path: Path) -> dict[str, object]:
    """Load a kicad-cli ``--severity-all --format json`` report file."""
    if not path.exists():
        raise KiCadCLIError(f"expected kicad-cli report at {path}, not found")
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data
