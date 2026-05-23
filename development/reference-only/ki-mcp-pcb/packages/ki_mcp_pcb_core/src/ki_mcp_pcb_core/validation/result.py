"""Shared result types for ERC / DRC reports."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Severity = Literal["error", "warning", "info", "exclusion"]


class Issue(BaseModel):
    severity: Severity
    type: str
    description: str
    items: list[str] = []


class CheckResult(BaseModel):
    """Outcome of an ERC or DRC run."""

    ok: bool
    errors: int
    warnings: int
    issues: list[Issue] = []
    report_path: Path | None = None
    raw_stdout: str = ""
    raw_stderr: str = ""


def cli_failure(kind: str, report_path: Path, stdout: str, stderr: str) -> CheckResult:
    """Build a :class:`CheckResult` for a kicad-cli run that produced no report.

    ``kicad-cli`` can exit 0 yet emit nothing — e.g. when it fails to load
    the input file ("Failed to load schematic"). Callers get this
    structured failure instead of an exception, preserving the "return a
    result, never raise" contract that ``run_erc`` / ``run_drc`` promise.
    """
    detail = (stderr or stdout or "").strip() or "kicad-cli produced no report"
    return CheckResult(
        ok=False,
        errors=1,
        warnings=0,
        issues=[
            Issue(
                severity="error",
                type="cli_failure",
                description=f"{kind} could not run: {detail}",
            )
        ],
        report_path=report_path,
        raw_stdout=stdout,
        raw_stderr=stderr,
    )
