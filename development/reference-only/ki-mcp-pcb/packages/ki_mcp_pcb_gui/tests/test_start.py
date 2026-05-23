"""Tests for the ki-mcp-pcb GUI launcher (``start.py``).

The process-handling helpers (``_spawn``/``_terminate``/``_wait_for_backend``)
are exercised against *real* short-lived child processes — that is the only
honest way to verify the process-group teardown actually works. The mode
dispatch in ``main`` is tested with the npm/backend seams stubbed.
"""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
import start

# A child that runs long enough to be killed; and one that exits at once.
_SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]
_QUICK_OK = [sys.executable, "-c", ""]
_QUICK_FAIL = [sys.executable, "-c", "raise SystemExit(3)"]


@pytest.fixture
def reap() -> Iterator[list[subprocess.Popen[bytes]]]:
    """Collect spawned processes and guarantee they're killed after the test."""
    spawned: list[subprocess.Popen[bytes]] = []
    yield spawned
    for proc in spawned:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# --------------------------------------------------------------------------
# _terminate — process-group teardown
# --------------------------------------------------------------------------
def test_terminate_stops_a_running_process(
    reap: list[subprocess.Popen[bytes]],
) -> None:
    proc = start._spawn(_SLEEPER)
    reap.append(proc)
    assert proc.poll() is None  # it's genuinely running

    start._terminate(proc)

    assert proc.poll() is not None  # ...and genuinely stopped


def test_terminate_is_a_noop_for_an_already_exited_process(
    reap: list[subprocess.Popen[bytes]],
) -> None:
    proc = start._spawn(_QUICK_OK)
    reap.append(proc)
    proc.wait(timeout=5)

    # Must not raise even though the process (and its group) are long gone.
    start._terminate(proc)
    assert proc.poll() == 0


# --------------------------------------------------------------------------
# _wait_for_backend — readiness polling
# --------------------------------------------------------------------------
def test_wait_for_backend_true_once_the_port_is_open(
    reap: list[subprocess.Popen[bytes]],
) -> None:
    # A real listening socket stands in for the backend's bound port.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        alive = start._spawn(_SLEEPER)
        reap.append(alive)
        assert start._wait_for_backend(alive, "127.0.0.1", port, timeout=3.0)
    finally:
        listener.close()


def test_wait_for_backend_false_when_the_process_dies(
    reap: list[subprocess.Popen[bytes]],
) -> None:
    dead = start._spawn(_QUICK_FAIL)
    reap.append(dead)
    dead.wait(timeout=5)

    # An unused, certainly-closed port — the only exit is the dead process.
    started = time.monotonic()
    ready = start._wait_for_backend(dead, "127.0.0.1", 9, timeout=10.0)
    assert ready is False
    # It bailed on the dead process, not by burning the full timeout.
    assert time.monotonic() - started < 5.0


# --------------------------------------------------------------------------
# _run_dev — backend + frontend orchestration
# --------------------------------------------------------------------------
def test_run_dev_aborts_when_the_backend_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(start, "_backend_available", lambda: False)
    assert start._run_dev("npm", with_backend=True) == 1


def test_run_dev_starts_both_then_tears_the_backend_down(
    monkeypatch: pytest.MonkeyPatch, reap: list[subprocess.Popen[bytes]]
) -> None:
    monkeypatch.setattr(start, "_backend_available", lambda: True)
    monkeypatch.setattr(start, "_wait_for_backend", lambda *a, **k: True)

    # First _spawn call is the backend (long-lived), second is the
    # "frontend" (exits 0 at once, so _run_dev's wait loop returns).
    real_spawn = start._spawn
    calls: list[subprocess.Popen[bytes]] = []

    def fake_spawn(cmd: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        proc = real_spawn(_SLEEPER if not calls else _QUICK_OK)
        calls.append(proc)
        reap.append(proc)
        return proc

    monkeypatch.setattr(start, "_spawn", fake_spawn)

    code = start._run_dev("npm", with_backend=True)

    assert code == 0  # propagated the frontend's clean exit
    assert len(calls) == 2  # backend + frontend both started
    backend = calls[0]
    assert backend.poll() is not None  # the backend was torn down


def test_sigterm_handler_raises_keyboard_interrupt() -> None:
    # A `kill` of the launcher must trigger the same teardown as Ctrl-C.
    with pytest.raises(KeyboardInterrupt):
        start._raise_keyboard_interrupt(signal.SIGTERM, None)


def test_run_dev_restores_the_previous_sigterm_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(start, "_backend_available", lambda: False)

    # _run_dev installs its own SIGTERM handler; it must put the old one back.
    start._run_dev("npm", with_backend=True)
    assert signal.getsignal(signal.SIGTERM) is sentinel


def test_run_dev_without_backend_starts_only_the_frontend(
    monkeypatch: pytest.MonkeyPatch, reap: list[subprocess.Popen[bytes]]
) -> None:
    backend_checked = False

    def guard() -> bool:
        nonlocal backend_checked
        backend_checked = True
        return True

    monkeypatch.setattr(start, "_backend_available", guard)

    real_spawn = start._spawn
    calls: list[subprocess.Popen[bytes]] = []

    def fake_spawn(cmd: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        proc = real_spawn(_QUICK_OK)
        calls.append(proc)
        reap.append(proc)
        return proc

    monkeypatch.setattr(start, "_spawn", fake_spawn)

    code = start._run_dev("npm", with_backend=False)

    assert code == 0
    assert len(calls) == 1  # only the Vite dev server
    assert backend_checked is False  # the backend path was skipped entirely


# --------------------------------------------------------------------------
# main — mode dispatch
# --------------------------------------------------------------------------
@pytest.fixture
def stub_npm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the npm-presence and dependency-install checks."""
    monkeypatch.setattr(start, "_require_npm", lambda: "npm")
    monkeypatch.setattr(start, "_ensure_dependencies", lambda npm: None)


def test_main_dev_dispatches_to_run_dev_with_the_backend(
    monkeypatch: pytest.MonkeyPatch, stub_npm: None
) -> None:
    seen: dict[str, object] = {}

    def fake_run_dev(npm: str, *, with_backend: bool) -> int:
        seen["npm"] = npm
        seen["with_backend"] = with_backend
        return 0

    monkeypatch.setattr(start, "_run_dev", fake_run_dev)

    assert start.main([]) == 0  # default mode is dev
    assert seen == {"npm": "npm", "with_backend": True}


def test_main_dev_no_backend_flag_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, stub_npm: None
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        start,
        "_run_dev",
        lambda npm, *, with_backend: seen.setdefault("with_backend", with_backend)
        or 0,
    )

    assert start.main(["dev", "--no-backend"]) == 0
    assert seen["with_backend"] is False


@pytest.mark.parametrize("mode", ["build", "preview"])
def test_main_build_and_preview_run_npm_only(
    monkeypatch: pytest.MonkeyPatch, stub_npm: None, mode: str
) -> None:
    ran: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        ran["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    # start.main calls subprocess.run — patching the module reaches it.
    monkeypatch.setattr(subprocess, "run", fake_run)
    # _run_dev must never be reached for build/preview.
    monkeypatch.setattr(
        start,
        "_run_dev",
        lambda *a, **k: pytest.fail("_run_dev called for a non-dev mode"),
    )

    assert start.main([mode]) == 0
    assert ran["cmd"] == ["npm", "run", mode]


def test_main_propagates_a_failing_build_exit_code(
    monkeypatch: pytest.MonkeyPatch, stub_npm: None
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 2),
    )
    assert start.main(["build"]) == 2
