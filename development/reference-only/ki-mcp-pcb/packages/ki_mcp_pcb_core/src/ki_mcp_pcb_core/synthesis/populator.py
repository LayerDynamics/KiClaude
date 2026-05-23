"""pcbnew-driven netlist → PCB populator.

The actual work lives in ``scripts/kicad_populate.py`` — that script
imports ``pcbnew``, which is bundled with KiCad and isn't installable
from PyPI. This module is the *wrapper*: it locates the right Python
interpreter (KiCad's bundled Python when available, otherwise the
current Python if ``pcbnew`` is on the path), invokes the script, and
returns a structured :class:`PopulateResult`.

Tests inject a fake runner via :func:`set_runner_for_tests` so unit
tests run without KiCad. The real end-to-end is covered by CI's
``kicad-build`` job.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ki_mcp_pcb_core.cir.models import FabTarget, Outline
    from ki_mcp_pcb_core.placement import Placement

# ---------------------------------------------------------------------------
# Errors + runner
# ---------------------------------------------------------------------------


class PopulatorError(RuntimeError):
    """Populator failed at the wrapper or subprocess level."""


class PCBNewNotAvailableError(PopulatorError):
    """No Python interpreter on this machine can ``import pcbnew``."""


Status = Literal["ok", "pcbnew_unavailable", "netlist_error", "load_error",
                 "footprint_missing", "unknown_error"]


@dataclass(frozen=True)
class PopulateResult:
    status: Status
    pcb_path: Path
    netlist_path: Path
    components_placed: int
    footprints_missing: list[str]
    errors: list[str]
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class _PopRun:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


_Runner = Callable[[Sequence[str]], _PopRun]


def _real_runner(argv: Sequence[str]) -> _PopRun:
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, PermissionError) as exc:
        # Treat "binary not on disk" the same as "binary exited non-zero",
        # so the candidate-probe loop in find_pcbnew_python can move on.
        return _PopRun(args=list(argv), returncode=127, stdout="", stderr=str(exc))
    return _PopRun(
        args=list(argv), returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )


_runner: _Runner = _real_runner


def set_runner_for_tests(runner: _Runner | None) -> _Runner:
    global _runner
    prev = _runner
    _runner = runner or _real_runner
    return prev


# ---------------------------------------------------------------------------
# Python-with-pcbnew discovery
# ---------------------------------------------------------------------------


def _candidate_pythons() -> list[str]:
    """Return ordered candidates for "the Python that can import pcbnew".

    Order: explicit env override → current interpreter → common KiCad-bundled
    locations on Linux/macOS/Windows.
    """
    explicit = os.environ.get("KICAD_PYTHON")
    candidates = [explicit] if explicit else []
    candidates.append(sys.executable)
    # KiCad-bundled Python paths we know about
    candidates.extend([
        "/usr/lib/kicad/bin/python3",
        "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
        "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe",
        shutil.which("python3") or "",
        shutil.which("python") or "",
    ])
    return [c for c in candidates if c]


def find_pcbnew_python() -> str:
    """Probe candidates until we find one that can ``import pcbnew``.

    Raises :class:`PCBNewNotAvailableError` if none do.
    """
    seen: set[str] = set()
    for py in _candidate_pythons():
        if py in seen:
            continue
        seen.add(py)
        result = _runner([py, "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"])
        if result.returncode == 0 and "pcbnew" not in (result.stderr or ""):
            return py
    raise PCBNewNotAvailableError(
        "No Python interpreter on this machine can `import pcbnew`. "
        "Install KiCad 9+ and set KICAD_PYTHON to its bundled Python, "
        "or run inside a KiCad-aware shell. See `kimp doctor`."
    )


def pcbnew_available() -> bool:
    try:
        find_pcbnew_python()
    except PCBNewNotAvailableError:
        return False
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[5] / "scripts" / "kicad_populate.py"
)


def _write_design_sidecar(
    pcb_path: Path,
    fab: FabTarget | None,
    outline: Outline | None,
    placements: Iterable[Placement] | None,
) -> Path | None:
    """Serialize the fab profile, board outline + planned placement.

    The script runs under KiCad's bundled Python, where the CIR package
    isn't importable — so we hand it a plain-JSON sidecar instead. Returns
    the sidecar path, or ``None`` when there's nothing to pass.
    """
    design: dict[str, object] = {}
    if fab is not None:
        design["fab"] = {
            "min_trace_mm": fab.min_trace_mm,
            "min_space_mm": fab.min_space_mm,
            "min_drill_mm": fab.min_drill_mm,
            "min_annular_ring_mm": fab.min_annular_ring_mm,
        }
    if outline is not None:
        design["outline"] = {
            "shape": outline.shape,
            "width_mm": outline.width_mm,
            "height_mm": outline.height_mm,
            "polygon_mm": (
                [list(point) for point in outline.polygon_mm]
                if outline.polygon_mm
                else None
            ),
            "corner_radius_mm": outline.corner_radius_mm,
        }
    if placements is not None:
        # Hint-aware coordinates from placement.plan_placement — the script
        # places each refdes here instead of on a blind grid.
        design["placements"] = {
            p.refdes: [p.x_mm, p.y_mm] for p in placements
        }
    if not design:
        return None
    sidecar = pcb_path.with_suffix(".design.json")
    sidecar.write_text(json.dumps(design, indent=2), encoding="utf-8")
    return sidecar


def populate(
    pcb_path: Path,
    netlist_path: Path,
    *,
    placement: str = "grid",
    spacing_mm: float = 15.0,
    report_path: Path | None = None,
    fab: FabTarget | None = None,
    outline: Outline | None = None,
    placements: Iterable[Placement] | None = None,
) -> PopulateResult:
    """Populate a PCB skeleton from a netlist using pcbnew under the hood.

    Returns a structured ``PopulateResult`` either way. If pcbnew isn't
    available, status is ``"pcbnew_unavailable"`` rather than raising —
    callers (pipeline.build) decide whether that's fatal.

    When ``fab`` / ``outline`` are supplied (the pipeline always does),
    the populate step also stamps the board's design rules and draws a
    closed Edge.Cuts outline — both required for the board to pass DRC.
    ``placements`` (from :func:`~ki_mcp_pcb_core.placement.plan_placement`)
    carries hint-aware coordinates; refdes without an entry fall back to a
    grid inside the script.
    """
    pcb_path = Path(pcb_path)
    netlist_path = Path(netlist_path)
    report_path = report_path or pcb_path.with_suffix(".populate.json")

    try:
        python = find_pcbnew_python()
    except PCBNewNotAvailableError as exc:
        return PopulateResult(
            status="pcbnew_unavailable",
            pcb_path=pcb_path,
            netlist_path=netlist_path,
            components_placed=0,
            footprints_missing=[],
            errors=[str(exc)],
            stdout="",
            stderr="",
        )

    argv = [
        python,
        str(_SCRIPT_PATH),
        "--pcb", str(pcb_path),
        "--net", str(netlist_path),
        "--placement", placement,
        "--spacing-mm", str(spacing_mm),
        "--report", str(report_path),
    ]
    design_sidecar = _write_design_sidecar(pcb_path, fab, outline, placements)
    if design_sidecar is not None:
        argv += ["--design-json", str(design_sidecar)]
    result = _runner(argv)
    report_data: dict[str, object] = {}
    if report_path.exists():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                report_data = loaded
        except json.JSONDecodeError:
            report_data = {}

    components_placed_raw = report_data.get("components_placed", 0)
    components_placed = int(components_placed_raw) if isinstance(components_placed_raw, int | str) else 0

    fp_missing_raw = report_data.get("footprints_missing") or []
    footprints_missing = [str(x) for x in fp_missing_raw] if isinstance(fp_missing_raw, list) else []

    errors_raw = report_data.get("errors") or []
    errors = [str(x) for x in errors_raw] if isinstance(errors_raw, list) else []

    status = _status_from_returncode(result.returncode)
    return PopulateResult(
        status=status,
        pcb_path=pcb_path,
        netlist_path=netlist_path,
        components_placed=components_placed,
        footprints_missing=footprints_missing,
        errors=errors,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _status_from_returncode(rc: int) -> Status:
    return {
        0: "ok",
        2: "pcbnew_unavailable",
        3: "netlist_error",
        4: "load_error",
        5: "footprint_missing",
    }.get(rc, "unknown_error")  # type: ignore[return-value]
