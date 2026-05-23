"""Canonical Intermediate Representation (CIR).

The typed electrical model every layer of ki-mcp-pcb agrees on. Stable
contract — schema changes bump ``CIR_VERSION`` and require a migration.
"""

from ki_mcp_pcb_core.cir.models import (
    CIR_VERSION,
    Board,
    Component,
    Constraint,
    FabTarget,
    Net,
    Outline,
    Stackup,
)
from ki_mcp_pcb_core.cir.validation import ValidationIssue, ValidationReport, validate_board

__all__ = [
    "CIR_VERSION",
    "Board",
    "Component",
    "Constraint",
    "FabTarget",
    "Net",
    "Outline",
    "Stackup",
    "ValidationIssue",
    "ValidationReport",
    "validate_board",
]
