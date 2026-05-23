"""Export: routed board → fab-ready artifacts.

  - Gerbers (RS-274X)
  - Excellon drill
  - BOM (CSV + IPC-2581)
  - Pick-and-place (CSV)
  - 3D STEP

Each verb lives in its own module under this package. ``fab_package`` is
the orchestrator that bundles them into a fab-target-specific zip.
"""

from ki_mcp_pcb_core.export.bom import BOMRow, write_bom_csv
from ki_mcp_pcb_core.export.fab_package import FabPackage, export_fab_package
from ki_mcp_pcb_core.export.gerbers import export_drill, export_gerbers
from ki_mcp_pcb_core.export.pick_and_place import export_pick_and_place
from ki_mcp_pcb_core.export.step import export_step

__all__ = [
    "BOMRow",
    "FabPackage",
    "export_drill",
    "export_fab_package",
    "export_gerbers",
    "export_pick_and_place",
    "export_step",
    "write_bom_csv",
]
