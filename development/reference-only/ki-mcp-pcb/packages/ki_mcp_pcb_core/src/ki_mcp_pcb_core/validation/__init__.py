"""Artifact validation: ERC (schematic) and DRC (board).

These wrap ``kicad-cli`` and run against generated KiCad files. CIR-level
structural validation lives separately under ``ki_mcp_pcb_core.cir.validation``.

The actual invocations live in :mod:`ki_mcp_pcb_core.validation.erc` and
:mod:`ki_mcp_pcb_core.validation.drc` — this module just re-exports the
public types.
"""

from ki_mcp_pcb_core.validation.drc import run_drc
from ki_mcp_pcb_core.validation.erc import run_erc
from ki_mcp_pcb_core.validation.result import CheckResult, Issue, Severity

__all__ = [
    "CheckResult",
    "Issue",
    "Severity",
    "run_drc",
    "run_erc",
]
