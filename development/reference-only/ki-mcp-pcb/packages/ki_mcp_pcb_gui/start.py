#!/usr/bin/env python3
"""Launcher for the ki-mcp-pcb GUI (Vite + React + TypeScript frontend).

The GUI proper is a Node/Vite project; this module is the Python entry
point that ties it into the uv workspace. ``uv run ki-mcp-pcb-gui`` — or
``python start.py`` — ensures the npm dependencies are installed and then
runs the requested Vite script.

In ``dev`` mode (the default) it also boots the ``ki-mcp-pcb-web`` FastAPI
backend the GUI's ``/api`` calls and the co-pilot WebSocket need, so a
single command brings the whole stack up — and takes it back down again
on exit. Pass ``--no-backend`` to run the backend yourself instead.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# The GUI's Node project lives in this file's own directory.
_GUI_DIR = Path(__file__).resolve().parent

# The bundled API backend binds here — it must match the `/api` proxy
# target in vite.config.ts (`server.proxy`), so it is not configurable.
_API_HOST = "127.0.0.1"
_API_PORT = 8765

# How long to wait for the backend to open its port before giving up.
_BACKEND_READY_TIMEOUT_S = 30.0

# How long a process gets to exit on SIGTERM before it is SIGKILLed.
_TERMINATE_GRACE_S = 5.0


def _require_npm() -> str:
    """Return the path to ``npm``, or exit with a helpful message."""
    npm = shutil.which("npm")
    if npm is None:
        sys.stderr.write(
            "npm not found on PATH. Install Node.js 20+ (https://nodejs.org) "
            "to run the ki-mcp-pcb GUI.\n"
        )
        raise SystemExit(1)
    return npm


def _ensure_dependencies(npm: str) -> None:
    """Run ``npm install`` when ``node_modules`` is absent."""
    if (_GUI_DIR / "node_modules").is_dir():
        return
    print("Installing GUI dependencies (npm install)...", flush=True)
    result = subprocess.run([npm, "install"], cwd=_GUI_DIR, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _backend_available() -> bool:
    """Return ``True`` when the ``ki-mcp-pcb-web`` backend can be imported."""
    import importlib.util

    return importlib.util.find_spec("ki_mcp_pcb_web") is not None


def _spawn(cmd: list[str], *, cwd: Path | None = None) -> subprocess.Popen[bytes]:
    """Start a child process in its own session so its whole tree can be
    signalled together (``start_new_session`` is a no-op off POSIX)."""
    return subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=(os.name == "posix"),
    )


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    """Stop a child process and its descendants, escalating if needed.

    On POSIX the whole process group is signalled, so Vite's esbuild
    workers and uvicorn's children go down with the parent.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=_TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass


def _wait_for_backend(
    proc: subprocess.Popen[bytes], host: str, port: int, *, timeout: float
) -> bool:
    """Poll ``host:port`` until it accepts a connection.

    Returns ``False`` as soon as ``proc`` exits (the backend died before it
    was ready) or when ``timeout`` elapses.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.25)
    return False


def _raise_keyboard_interrupt(signum: int, frame: object) -> None:
    """SIGTERM handler — re-raise it as ``KeyboardInterrupt`` so a ``kill``
    (or a process manager) triggers the same clean teardown as Ctrl-C."""
    raise KeyboardInterrupt


def _run_dev(npm: str, *, with_backend: bool) -> int:
    """Run the dev stack: the API backend (optional) plus the Vite server.

    Both processes run until one exits or the user interrupts (Ctrl-C *or*
    SIGTERM); whichever happens, the other is always torn down before this
    returns.
    """
    procs: list[tuple[str, subprocess.Popen[bytes]]] = []
    prev_sigterm = signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        if with_backend:
            if not _backend_available():
                sys.stderr.write(
                    "ki-mcp-pcb-web is not installed, so the GUI's API "
                    "backend can't start. Install it with "
                    "`uv sync --extra web`, or run with `--no-backend` and "
                    "start the backend yourself.\n"
                )
                return 1
            print(
                f"Starting API backend on http://{_API_HOST}:{_API_PORT} ...",
                flush=True,
            )
            backend = _spawn(
                [sys.executable, "-c", "from ki_mcp_pcb_web.server import run; run()"]
            )
            procs.append(("API backend", backend))
            if not _wait_for_backend(
                backend, _API_HOST, _API_PORT, timeout=_BACKEND_READY_TIMEOUT_S
            ):
                if backend.poll() is not None:
                    sys.stderr.write("API backend exited before it was ready.\n")
                else:
                    sys.stderr.write(
                        f"API backend did not open port {_API_PORT} within "
                        f"{_BACKEND_READY_TIMEOUT_S:.0f}s.\n"
                    )
                return 1
            print("API backend ready.", flush=True)

        print("Starting Vite dev server...", flush=True)
        frontend = _spawn([npm, "run", "dev"], cwd=_GUI_DIR)
        procs.append(("Vite dev server", frontend))

        # Run until any process exits; report which, and propagate its code.
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    if code != 0:
                        sys.stderr.write(f"{name} exited with code {code}.\n")
                    else:
                        print(f"{name} exited.", flush=True)
                    return code
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\nShutting down the dev stack...", flush=True)
        return 0
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        # Tear down in reverse start order — frontend first, then backend.
        for name, proc in reversed(procs):
            if proc.poll() is None:
                print(f"Stopping {name}...", flush=True)
            _terminate(proc)


def main(argv: list[str] | None = None) -> int:
    """Ensure dependencies, then run the requested mode.

    Returns the relevant subprocess's exit code so the process status
    propagates to ``uv run`` / the shell.
    """
    parser = argparse.ArgumentParser(
        description="Run the ki-mcp-pcb GUI (Vite + React frontend)."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="dev",
        choices=["dev", "build", "preview"],
        help=(
            "dev: start the API backend + the Vite dev server with HMR "
            "(default); build: produce a production bundle in dist/; "
            "preview: serve a previously built bundle."
        ),
    )
    parser.add_argument(
        "--no-backend",
        action="store_true",
        help=(
            "In dev mode, don't start the bundled ki-mcp-pcb-web backend — "
            "run it yourself (e.g. `uv run ki-mcp-pcb-web`)."
        ),
    )
    args = parser.parse_args(argv)

    npm = _require_npm()
    _ensure_dependencies(npm)

    if args.mode == "dev":
        return _run_dev(npm, with_backend=not args.no_backend)

    # build / preview are frontend-only — no API backend involved.
    completed = subprocess.run([npm, "run", args.mode], cwd=_GUI_DIR, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
