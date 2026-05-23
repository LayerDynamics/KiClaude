"""In-memory project registry — keyed by UUID4 project_id."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class OpenedProject:
    """One open project — its on-disk path, generated id, and the
    full KCIR `Project` dict returned by `ki_native.open_project`.
    """

    project_id: str
    path: Path
    project: dict[str, Any]

    @property
    def summary(self) -> dict[str, Any]:
        """Compact summary shape returned by `POST /project/open`."""
        pcb = self.project.get("pcb", {})
        return {
            "name": self.project.get("name", ""),
            "kcir_version": self.project.get("kcir_version", ""),
            "layer_count": len(pcb.get("layers", [])),
            "footprint_count": len(pcb.get("footprints", [])),
            "track_count": len(pcb.get("tracks", [])),
            "via_count": len(pcb.get("vias", [])),
            "zone_count": len(pcb.get("zones", [])),
            "net_count": len(pcb.get("nets", [])),
        }


@dataclass(slots=True)
class ProjectRegistry:
    """Thread-safe in-memory map of `project_id` → `OpenedProject`.
    Reset between FastAPI test runs by constructing a fresh instance.
    """

    _projects: dict[str, OpenedProject] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def insert(self, project: dict[str, Any], path: Path) -> OpenedProject:
        """Wrap an open project with a fresh UUID4 and store it."""
        opened = OpenedProject(
            project_id=str(uuid.uuid4()),
            path=path,
            project=project,
        )
        with self._lock:
            self._projects[opened.project_id] = opened
        return opened

    def get(self, project_id: str) -> OpenedProject | None:
        with self._lock:
            return self._projects.get(project_id)

    def replace(self, project_id: str, project: dict[str, Any]) -> OpenedProject | None:
        """Swap the stored KCIR project dict for a (typically mutated)
        one. Returns the new entry, or None if the project_id is
        unknown."""
        with self._lock:
            existing = self._projects.get(project_id)
            if existing is None:
                return None
            replaced = OpenedProject(
                project_id=existing.project_id,
                path=existing.path,
                project=project,
            )
            self._projects[project_id] = replaced
            return replaced

    def clear(self) -> None:
        with self._lock:
            self._projects.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._projects)


# Module-level registry instance used by `main.py`. Tests can construct
# their own and inject via `app.dependency_overrides`.
REGISTRY = ProjectRegistry()


__all__ = ["REGISTRY", "OpenedProject", "ProjectRegistry"]
