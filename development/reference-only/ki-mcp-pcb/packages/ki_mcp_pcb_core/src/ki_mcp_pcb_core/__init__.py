"""ki-mcp-pcb core library.

Plain-text → manufacturable KiCad PCB pipeline. See ``SPEC.md`` at the repo
root for the project specification.

Public re-exports are kept intentionally small. Reach into submodules
(``ki_mcp_pcb_core.cir``, ``.parsers``, ``.synthesis``, ...) for everything
else; that's the supported API surface.
"""

from __future__ import annotations

from ki_mcp_pcb_core.cir.models import (
    Board,
    Component,
    Constraint,
    FabTarget,
    Net,
    Stackup,
)

__all__ = [
    "Board",
    "Component",
    "Constraint",
    "FabTarget",
    "Net",
    "Stackup",
    "__version__",
]

__version__ = "0.0.1"
