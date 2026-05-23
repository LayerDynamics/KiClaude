"""ERC wrapper — ``kicad-cli sch erc``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ki_mcp_pcb_core._kicad_cli import (
    read_kicad_json_report,
    run_kicad_cli,
)
from ki_mcp_pcb_core.validation.result import CheckResult, Issue, cli_failure


def run_erc(schematic_path: Path, *, report_path: Path | None = None) -> CheckResult:
    """Run ERC on a ``.kicad_sch`` file via ``kicad-cli sch erc``.

    Returns a :class:`CheckResult`. If ``report_path`` is not provided we
    write the JSON report next to the schematic.
    """
    schematic_path = Path(schematic_path)
    if not schematic_path.exists():
        raise FileNotFoundError(schematic_path)

    report_path = report_path or schematic_path.with_suffix(".erc.json")

    completed = run_kicad_cli(
        [
            "sch",
            "erc",
            "--severity-all",
            "--format",
            "json",
            "-o",
            str(report_path),
            str(schematic_path),
        ],
        check=False,  # kicad-cli returns non-zero on ERC violations; we handle that here
    )

    if not report_path.exists():
        return cli_failure("ERC", report_path, completed.stdout, completed.stderr)

    issues, errors, warnings = _parse_report(report_path)
    return CheckResult(
        ok=errors == 0,
        errors=errors,
        warnings=warnings,
        issues=issues,
        report_path=report_path,
        raw_stdout=completed.stdout,
        raw_stderr=completed.stderr,
    )


def _parse_report(report_path: Path) -> tuple[list[Issue], int, int]:
    data: dict[str, Any] = cast(dict[str, Any], read_kicad_json_report(report_path))
    issues: list[Issue] = []
    errors = 0
    warnings = 0

    # KiCad 9 JSON shape for sch erc: { "sheets": [ { "violations": [ ... ] }, ... ] }
    # Some KiCad versions emit "violations" at the top level instead.
    sheets = data.get("sheets") or [{"violations": data.get("violations", [])}]
    for sheet in sheets:
        for v in sheet.get("violations", []):
            sev = v.get("severity", "error")
            issue = Issue(
                severity=sev,
                type=v.get("type", "unknown"),
                description=v.get("description", ""),
                items=[it.get("description", "") for it in v.get("items", [])],
            )
            issues.append(issue)
            if sev == "error":
                errors += 1
            elif sev == "warning":
                warnings += 1
    return issues, errors, warnings
