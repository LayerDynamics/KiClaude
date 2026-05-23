"""DRC wrapper — ``kicad-cli pcb drc``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ki_mcp_pcb_core._kicad_cli import (
    read_kicad_json_report,
    run_kicad_cli,
)
from ki_mcp_pcb_core.validation.result import CheckResult, Issue, cli_failure


def run_drc(
    pcb_path: Path,
    *,
    report_path: Path | None = None,
    expect_routed: bool = True,
) -> CheckResult:
    """Run DRC on a ``.kicad_pcb`` file via ``kicad-cli pcb drc``.

    ``expect_routed`` controls how ratsnest completeness is scored. When
    ``True`` (the default — the strict, fail-closed setting) missing
    connections are errors. When ``False`` the caller knows the board has
    been populated but not yet routed, so ``unconnected_items`` are
    demoted to warnings. Rule ``violations`` stay fatal either way.
    """
    pcb_path = Path(pcb_path)
    if not pcb_path.exists():
        raise FileNotFoundError(pcb_path)

    report_path = report_path or pcb_path.with_suffix(".drc.json")

    completed = run_kicad_cli(
        [
            "pcb",
            "drc",
            "--severity-all",
            "--format",
            "json",
            "-o",
            str(report_path),
            str(pcb_path),
        ],
        check=False,
    )

    if not report_path.exists():
        return cli_failure("DRC", report_path, completed.stdout, completed.stderr)

    issues, errors, warnings = _parse_report(report_path, expect_routed=expect_routed)
    return CheckResult(
        ok=errors == 0,
        errors=errors,
        warnings=warnings,
        issues=issues,
        report_path=report_path,
        raw_stdout=completed.stdout,
        raw_stderr=completed.stderr,
    )


def _parse_report(
    report_path: Path, *, expect_routed: bool = True
) -> tuple[list[Issue], int, int]:
    data: dict[str, Any] = cast(dict[str, Any], read_kicad_json_report(report_path))
    issues: list[Issue] = []
    errors = 0
    warnings = 0

    # KiCad 9 pcb drc JSON shape: top-level "violations", "unconnected_items",
    # "schematic_parity" lists.
    buckets = ("violations", "unconnected_items", "schematic_parity")
    for bucket in buckets:
        for v in data.get(bucket, []):
            sev = v.get("severity", "error")
            # "unconnected_items" reports ratsnest completeness, not a design-
            # rule breach. For a board that's been populated but not yet
            # routed it is expected to be non-zero, so a pre-route caller
            # (expect_routed=False) sees it demoted to a warning. This is
            # categorization, not a DRC bypass: real "violations" and
            # "schematic_parity" defects stay fatal in every mode.
            if bucket == "unconnected_items" and not expect_routed and sev == "error":
                sev = "warning"
            issue = Issue(
                severity=sev,
                type=v.get("type", bucket),
                description=v.get("description", ""),
                items=[it.get("description", "") for it in v.get("items", [])],
            )
            issues.append(issue)
            if sev == "error":
                errors += 1
            elif sev == "warning":
                warnings += 1
    return issues, errors, warnings
