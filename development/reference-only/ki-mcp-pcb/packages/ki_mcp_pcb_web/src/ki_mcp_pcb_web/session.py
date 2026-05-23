"""GUI working session — the single working directory + working CIR file.

Local single-user model (SPEC-1 §3.2): the GUI co-pilot operates on one
*working directory* holding one *working CIR file*; pipeline builds write
into a ``build/`` subdirectory of it.

Resolution order (highest precedence first):

1. ``KIMP_GUI_WORKDIR`` environment variable — explicit override, never
   persisted.
2. The persisted choice from ``~/.config/ki-mcp-pcb/session.json``,
   written by the GUI's "Open workspace…" control (SPEC-1 G4).
3. ``./gui-workspace`` relative to the process's current directory —
   the original default, kept for the zero-configuration case.

The persistence file location can be overridden with
``KIMP_GUI_SESSION_FILE`` (used by the test suite).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

#: Environment variable that overrides the working-directory location.
WORKDIR_ENV = "KIMP_GUI_WORKDIR"

#: Environment variable that overrides the built-GUI ``dist`` location.
GUI_DIST_ENV = "KIMP_GUI_DIST"

#: Environment variable that overrides the persistence-file location.
SESSION_FILE_ENV = "KIMP_GUI_SESSION_FILE"

_DEFAULT_WORKDIR_NAME = "gui-workspace"
_CIR_FILENAME = "board.cir.yaml"
_BUILD_DIRNAME = "build"
_DEFAULT_SESSION_FILE = Path.home() / ".config" / "ki-mcp-pcb" / "session.json"

#: What ``working_dir()`` is currently resolving from — surfaced by the
#: ``GET /api/workspace`` endpoint so the GUI can render the right source
#: (env-overridden vs. persisted vs. default).
WorkspaceSource = Literal["env", "persisted", "default"]


def _session_file() -> Path:
    """Resolve the persistence file (overridable for tests)."""
    override = os.environ.get(SESSION_FILE_ENV)
    return Path(override) if override else _DEFAULT_SESSION_FILE


def read_persisted_workdir() -> Path | None:
    """Return the persisted working directory, or ``None`` when absent.

    A malformed JSON or a missing ``last_workdir`` key falls through to
    ``None`` so a corrupted session file never breaks the launcher.
    """
    path = _session_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("last_workdir") if isinstance(data, dict) else None
    if not isinstance(raw, str) or raw == "":
        return None
    return Path(raw)


def write_persisted_workdir(workdir: Path) -> None:
    """Persist ``workdir`` as the new last-workdir choice.

    Creates the parent directory tree if needed. The file is written
    atomically (temp + rename) so a crash mid-write can't corrupt it.
    """
    path = _session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"last_workdir": str(workdir)})
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def working_dir_source() -> WorkspaceSource:
    """Return which resolution rule chose the current working directory."""
    if os.environ.get(WORKDIR_ENV):
        return "env"
    if read_persisted_workdir() is not None:
        return "persisted"
    return "default"


def working_dir() -> Path:
    """Return the GUI working directory, creating it if absent.

    Resolves in the documented order: env override > persisted choice >
    ``./gui-workspace``.
    """
    override = os.environ.get(WORKDIR_ENV)
    if override:
        base = Path(override)
    else:
        persisted = read_persisted_workdir()
        base = persisted if persisted is not None else Path.cwd() / _DEFAULT_WORKDIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def cir_path() -> Path:
    """Return the path of the working CIR file (which may not exist yet)."""
    return working_dir() / _CIR_FILENAME


def build_dir() -> Path:
    """Return the directory pipeline builds write into, creating it if absent."""
    path = working_dir() / _BUILD_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def gui_dist_dir() -> Path | None:
    """Return the built GUI's ``dist`` directory, or ``None`` if not built.

    Defaults to ``packages/ki_mcp_pcb_gui/dist`` relative to this file (the
    uv-workspace layout); overridable via ``KIMP_GUI_DIST``.
    """
    override = os.environ.get(GUI_DIST_ENV)
    if override:
        candidate = Path(override)
    else:
        # session.py → ki_mcp_pcb_web/src/ki_mcp_pcb_web/ — parents[3] is
        # the `packages/` directory.
        candidate = Path(__file__).resolve().parents[3] / "ki_mcp_pcb_gui" / "dist"
    return candidate if (candidate / "index.html").is_file() else None
