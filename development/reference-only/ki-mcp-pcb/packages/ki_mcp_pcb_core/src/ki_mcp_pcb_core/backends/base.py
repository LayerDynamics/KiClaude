"""Backend interface.

A ``Backend`` knows how to materialize CIR into an EDA tool's native
files. Only one ships in v1 (KiCad) — see ``kicad.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ki_mcp_pcb_core.cir.models import Board


class Backend(ABC):
    """Adapter contract every EDA backend must implement."""

    name: str

    @abstractmethod
    def write_project(self, board: Board, out_dir: Path) -> Path:
        """Materialize the CIR into a project on disk. Returns the project file path."""

    @abstractmethod
    def read_project(self, project_path: Path) -> Board:
        """Round-trip: read a backend project back into CIR."""

    @abstractmethod
    def run_erc(self, project_path: Path) -> tuple[int, int]:
        """Return (errors, warnings)."""

    @abstractmethod
    def run_drc(self, project_path: Path) -> tuple[int, int]:
        """Return (errors, warnings)."""
