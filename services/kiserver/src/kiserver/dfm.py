"""DFM (Design-for-Manufacture) dry-run rules — M2-T-09 pre-flight +
M2-Q-03 board-house gate.

The PCB editor's `FabExportDialog` calls `run_dfm_check(project,
target)` before invoking `kc_export_fab`, surfacing rule violations
(`error`) and weaker recommendations (`warning`) so the user can fix
problems before files are written to disk.

Rule presets carry per-house minima distilled from each fab's public
specifications (JLCPCB Standard PCB rules, OSHPark 4-Layer specs,
PCBWay Standard PCB capabilities). The values are conservative — if
a real spec advances the floor, the constants here are easy to
update and the rest of the pipeline does not need to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class FabRules:
    """A board-house's manufacturing minima. All values in mm."""

    target: str
    """Display name (`jlcpcb`, `oshpark`, etc.)."""
    min_track_mm: float
    """Hard minimum track width."""
    min_clearance_mm: float
    """Hard minimum copper-to-copper clearance."""
    min_via_drill_mm: float
    """Smallest mechanically-drillable via hole."""
    min_via_diameter_mm: float
    """Smallest annular ring around a via."""
    min_silk_width_mm: float
    """Thinnest silkscreen line the printer can resolve."""
    min_silk_height_mm: float
    """Minimum legible text height."""
    min_edge_to_copper_mm: float
    """Closest copper can be to the board edge."""
    advise_track_mm: float
    """Below this width, surface a `warning` (not blocking)."""


# Conservative subset of each fab's public spec — sourced from:
#   * JLCPCB Capabilities (https://jlcpcb.com/capabilities/pcb-capabilities)
#   * OSHPark 2/4-Layer specs (https://docs.oshpark.com/services/)
#   * PCBWay Standard PCB (https://www.pcbway.com/capabilities.html)
PRESETS: dict[str, FabRules] = {
    "jlcpcb": FabRules(
        target="jlcpcb",
        min_track_mm=0.127,
        min_clearance_mm=0.127,
        min_via_drill_mm=0.3,
        min_via_diameter_mm=0.45,
        min_silk_width_mm=0.153,
        min_silk_height_mm=1.0,
        min_edge_to_copper_mm=0.3,
        advise_track_mm=0.2,
    ),
    "oshpark": FabRules(
        target="oshpark",
        min_track_mm=0.1524,  # 6 mil
        min_clearance_mm=0.1524,
        min_via_drill_mm=0.3302,  # 13 mil
        min_via_diameter_mm=0.6604,  # 26 mil
        min_silk_width_mm=0.1524,
        min_silk_height_mm=1.0,
        min_edge_to_copper_mm=0.381,  # 15 mil
        advise_track_mm=0.2,
    ),
    "pcbway": FabRules(
        target="pcbway",
        min_track_mm=0.0889,  # 3.5 mil
        min_clearance_mm=0.0889,
        min_via_drill_mm=0.15,
        min_via_diameter_mm=0.3,
        min_silk_width_mm=0.1,
        min_silk_height_mm=0.85,
        min_edge_to_copper_mm=0.2,
        advise_track_mm=0.15,
    ),
    "generic": FabRules(
        target="generic",
        # The "generic" preset uses the loosest acceptable
        # minima across our supported fabs — a board that passes
        # `generic` should fit any house, but the user is
        # responsible for re-checking against their fab's spec.
        min_track_mm=0.2,
        min_clearance_mm=0.2,
        min_via_drill_mm=0.3,
        min_via_diameter_mm=0.6,
        min_silk_width_mm=0.2,
        min_silk_height_mm=1.0,
        min_edge_to_copper_mm=0.5,
        advise_track_mm=0.25,
    ),
}


@dataclass(frozen=True)
class DfmIssue:
    """One DFM finding. The editor surfaces these in the export
    dialog; the user must clear all `error`-severity issues before
    `kc_export_fab` is allowed to run."""

    severity: Severity
    rule: str
    """Short rule key (`min_track`, `min_via_drill`, …)."""
    description: str
    items: list[str]
    """Identifying refs — `["F.Cu", "track:abc-123"]` etc."""
    actual_mm: float
    limit_mm: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "description": self.description,
            "items": list(self.items),
            "actual_mm": self.actual_mm,
            "limit_mm": self.limit_mm,
        }


def known_targets() -> list[str]:
    return sorted(PRESETS.keys())


def get_preset(target: str) -> FabRules:
    target = (target or "generic").lower()
    if target not in PRESETS:
        raise KeyError(
            f"unknown fab target {target!r}; choose from {known_targets()}"
        )
    return PRESETS[target]


def run_dfm_check(
    project: dict[str, Any], target: str
) -> dict[str, Any]:
    """Apply `target`'s preset against the loaded project. Returns
    `{ok, target, issues:[...], counts:{error, warning}}`.

    `ok` is `True` iff there are no `error`-severity findings —
    warnings are non-blocking per the M2-Q-03 contract.
    """
    rules = get_preset(target)
    pcb = project.get("pcb") or {}
    issues: list[DfmIssue] = []

    # Track-width checks: every track segment narrower than the
    # hard min is an error; below `advise_track_mm` is a warning.
    for track in pcb.get("tracks", []) or []:
        width = float(track.get("width_mm") or 0.0)
        layer = track.get("layer") or "F.Cu"
        uuid = track.get("uuid", "?")
        if width <= 0:
            continue
        if width < rules.min_track_mm:
            issues.append(
                DfmIssue(
                    severity="error",
                    rule="min_track",
                    description=(
                        f"track width {width:.4f} mm on {layer} is below "
                        f"{rules.target}'s {rules.min_track_mm:.4f} mm minimum"
                    ),
                    items=[layer, f"track:{uuid}"],
                    actual_mm=width,
                    limit_mm=rules.min_track_mm,
                )
            )
        elif width < rules.advise_track_mm:
            issues.append(
                DfmIssue(
                    severity="warning",
                    rule="advise_track",
                    description=(
                        f"track width {width:.4f} mm on {layer} is below "
                        f"the recommended {rules.advise_track_mm:.4f} mm — "
                        "consider widening for yield"
                    ),
                    items=[layer, f"track:{uuid}"],
                    actual_mm=width,
                    limit_mm=rules.advise_track_mm,
                )
            )

    # Via drill + diameter checks.
    for via in pcb.get("vias", []) or []:
        drill = float(via.get("drill_mm") or 0.0)
        dia = float(via.get("diameter_mm") or 0.0)
        uuid = via.get("uuid", "?")
        if drill > 0 and drill < rules.min_via_drill_mm:
            issues.append(
                DfmIssue(
                    severity="error",
                    rule="min_via_drill",
                    description=(
                        f"via drill {drill:.4f} mm is below "
                        f"{rules.target}'s {rules.min_via_drill_mm:.4f} mm minimum"
                    ),
                    items=[f"via:{uuid}"],
                    actual_mm=drill,
                    limit_mm=rules.min_via_drill_mm,
                )
            )
        if dia > 0 and dia < rules.min_via_diameter_mm:
            issues.append(
                DfmIssue(
                    severity="error",
                    rule="min_via_diameter",
                    description=(
                        f"via diameter {dia:.4f} mm is below "
                        f"{rules.target}'s {rules.min_via_diameter_mm:.4f} mm minimum"
                    ),
                    items=[f"via:{uuid}"],
                    actual_mm=dia,
                    limit_mm=rules.min_via_diameter_mm,
                )
            )

    # Net-class clearance checks: each declared class's clearance
    # must meet the fab's minimum or surface as an error.
    for cls in pcb.get("net_classes", []) or []:
        clearance = float(cls.get("clearance_mm") or 0.0)
        if clearance > 0 and clearance < rules.min_clearance_mm:
            issues.append(
                DfmIssue(
                    severity="error",
                    rule="min_clearance",
                    description=(
                        f"net-class {cls.get('name', '?')!r} clearance "
                        f"{clearance:.4f} mm is below "
                        f"{rules.target}'s {rules.min_clearance_mm:.4f} mm minimum"
                    ),
                    items=[f"net_class:{cls.get('name', '?')}"],
                    actual_mm=clearance,
                    limit_mm=rules.min_clearance_mm,
                )
            )

    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    return {
        "ok": error_count == 0,
        "target": rules.target,
        "issues": [issue.to_dict() for issue in issues],
        "counts": {"error": error_count, "warning": warning_count},
    }


__all__ = [
    "PRESETS",
    "DfmIssue",
    "FabRules",
    "get_preset",
    "known_targets",
    "run_dfm_check",
]
