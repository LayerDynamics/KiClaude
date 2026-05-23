"""Per-project ClaudeSDKClient sessions (M1-P-07).

[`SessionManager`][SessionManager] holds one
[`ClaudeSDKClient`][claude_agent_sdk.ClaudeSDKClient] per
`project_id`, captures the session id from the SDK's
`SystemMessage(subtype="init")` first frame, and persists a small
JSON manifest at
`<project>/.kiclaude/sessions/<session_id>.json` so closing and
reopening a tab restarts the agent on the same session id with
[`ClaudeAgentOptions.resume`][claude_agent_sdk.ClaudeAgentOptions].

The module separates I/O from the SDK call so the persistence path
can be unit-tested without a live `ANTHROPIC_API_KEY`. The full
"open project → chat → close → reopen → chat history intact" flow
exercises the persisted manifest in the M1-Q-03 Playwright spec.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def claude_sdk_available() -> bool:
    """`True` if `claude_agent_sdk` can be imported in the current env."""
    try:
        import claude_agent_sdk  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass(slots=True)
class SessionManifest:
    """On-disk JSON shape persisted at
    `<project>/.kiclaude/sessions/<session_id>.json`."""

    project_id: str
    session_id: str
    project_path: str
    started_at_unix: float = field(default_factory=time.time)
    last_seen_at_unix: float = field(default_factory=time.time)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionManifest:
        return cls(
            project_id=str(data.get("project_id", "")),
            session_id=str(data.get("session_id", "")),
            project_path=str(data.get("project_path", "")),
            started_at_unix=float(data.get("started_at_unix", time.time())),
            last_seen_at_unix=float(data.get("last_seen_at_unix", time.time())),
            schema_version=int(data.get("schema_version", 1)),
        )


def manifests_dir(project_path: Path) -> Path:
    return project_path / ".kiclaude" / "sessions"


def write_manifest(manifest: SessionManifest) -> Path:
    """Atomically persist `manifest` to
    `<project>/.kiclaude/sessions/<session_id>.json`. Returns the path
    written."""
    project_path = Path(manifest.project_path)
    dir_path = manifests_dir(project_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    target = dir_path / f"{manifest.session_id}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest.to_dict(), sort_keys=True, indent=2))
    tmp.replace(target)
    return target


def load_manifest(project_path: Path, session_id: str) -> SessionManifest | None:
    """Load one manifest by `session_id`. Returns None when absent."""
    path = manifests_dir(project_path) / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        return SessionManifest.from_dict(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError):
        return None


def list_manifests(project_path: Path) -> list[SessionManifest]:
    """Every manifest under the project's `.kiclaude/sessions/`,
    sorted by `last_seen_at_unix` descending (newest first). Useful
    for the reopen flow's "most recent session" pick."""
    dir_path = manifests_dir(project_path)
    if not dir_path.is_dir():
        return []
    out: list[SessionManifest] = []
    for entry in dir_path.glob("*.json"):
        try:
            out.append(SessionManifest.from_dict(json.loads(entry.read_text())))
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda m: m.last_seen_at_unix, reverse=True)
    return out


def most_recent_session_id(project_path: Path) -> str | None:
    """Return the latest session_id for `project_path`, or None when
    the project has never been opened."""
    history = list_manifests(project_path)
    return history[0].session_id if history else None


def touch_manifest(project_path: Path, session_id: str) -> SessionManifest | None:
    """Bump `last_seen_at_unix` on an existing manifest. Returns the
    updated manifest or None if no manifest exists."""
    manifest = load_manifest(project_path, session_id)
    if manifest is None:
        return None
    manifest.last_seen_at_unix = time.time()
    write_manifest(manifest)
    return manifest


@dataclass(slots=True)
class _SessionEntry:
    """One live session — paired with the SDK client when started."""

    project_id: str
    project_path: Path
    session_id: str | None = None
    client: Any = None  # actual ClaudeSDKClient when running
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionManager:
    """In-memory map `project_id → _SessionEntry`.

    Implements the M1-P-07 lifecycle:

    - `open_session(project_id, project_path)` returns the matching
      entry, restoring the most-recent session id from disk if one
      exists.
    - `start_client(entry, prompt)` (used by tests via injection)
      drives one ClaudeSDKClient turn, captures
      `SystemMessage(subtype="init").session_id`, persists the
      manifest, and returns the assistant text.
    - `close(project_id)` releases the client.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _SessionEntry] = {}
        self._lock = asyncio.Lock()

    async def open_session(
        self, project_id: str, project_path: str | Path
    ) -> _SessionEntry:
        path = Path(project_path).expanduser().resolve()
        async with self._lock:
            entry = self._entries.get(project_id)
            if entry is None:
                entry = _SessionEntry(project_id=project_id, project_path=path)
                self._entries[project_id] = entry
            else:
                entry.project_path = path
            if entry.session_id is None:
                entry.session_id = most_recent_session_id(path)
            return entry

    async def close(self, project_id: str) -> None:
        async with self._lock:
            entry = self._entries.pop(project_id, None)
        if entry is not None and entry.client is not None:
            close_fn = getattr(entry.client, "close", None)
            if callable(close_fn):
                result = close_fn()
                if asyncio.iscoroutine(result):
                    await result

    def entry(self, project_id: str) -> _SessionEntry | None:
        return self._entries.get(project_id)

    async def chat(
        self,
        project_id: str,
        project_path: str | Path,
        prompt: str,
        *,
        client_factory: Any = None,
    ) -> dict[str, Any]:
        """Send one prompt through the per-project session. Captures
        the SDK init frame's session_id on first reply and persists
        the manifest so the next call resumes the same conversation.
        """
        entry = await self.open_session(project_id, project_path)
        async with entry.lock:
            factory = client_factory or _default_client_factory
            if entry.client is None:
                entry.client = await factory(
                    project_path=entry.project_path,
                    resume_session_id=entry.session_id,
                )
            client = entry.client
            try:
                await client.query(prompt)
                reply_parts: list[str] = []
                init_session_id: str | None = None
                async for msg in client.receive_response():
                    init_id = _extract_init_session_id(msg)
                    if init_id and not init_session_id:
                        init_session_id = init_id
                    reply_parts.extend(_collect_assistant_text(msg))
                if init_session_id and entry.session_id != init_session_id:
                    entry.session_id = init_session_id
                    write_manifest(
                        SessionManifest(
                            project_id=project_id,
                            session_id=init_session_id,
                            project_path=str(entry.project_path),
                        )
                    )
                elif entry.session_id:
                    touch_manifest(entry.project_path, entry.session_id)
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "project_id": project_id,
                    "session_id": entry.session_id,
                }
        return {
            "ok": True,
            "project_id": project_id,
            "session_id": entry.session_id,
            "reply": "".join(reply_parts),
        }


async def _default_client_factory(
    *, project_path: Path, resume_session_id: str | None
) -> Any:
    """Real client builder — wires `build_options` with the
    `resume=<session_id>` option when one was captured before."""
    from claude_agent_sdk import ClaudeSDKClient

    from agent.bridge import build_options

    options = build_options()
    if resume_session_id:
        options.resume = resume_session_id
    options.cwd = str(project_path)
    client = ClaudeSDKClient(options=options)
    await client.__aenter__()
    return client


def _extract_init_session_id(msg: Any) -> str | None:
    """Pull `session_id` off a `SystemMessage(subtype="init")` from
    the Claude Agent SDK message stream."""
    if getattr(msg, "subtype", None) == "init":
        sid = getattr(msg, "session_id", None)
        if isinstance(sid, str) and sid:
            return sid
        data = getattr(msg, "data", None)
        if isinstance(data, dict):
            sid = data.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
    return None


def _collect_assistant_text(msg: Any) -> list[str]:
    """Pull TextBlock.text strings off the assistant message."""
    try:
        from claude_agent_sdk import AssistantMessage, TextBlock
    except ImportError:
        return []
    if not isinstance(msg, AssistantMessage):
        return []
    out: list[str] = []
    for block in getattr(msg, "content", []) or []:
        if isinstance(block, TextBlock):
            out.append(block.text)
    return out


# Module-level default manager used by the FastAPI handlers.
DEFAULT_MANAGER = SessionManager()


async def run_session(prompt: str) -> dict[str, Any]:
    """Backwards-compatible one-shot helper used by `/echo` in
    `agent.main`. Uses an ephemeral session under a fixed
    `project_id="default"` rooted at the agent's CWD."""
    if not claude_sdk_available():
        return {"ok": False, "reply": "claude_agent_sdk not installed"}
    cwd = Path.cwd()
    result = await DEFAULT_MANAGER.chat("default", cwd, prompt)
    if not result.get("ok"):
        return {"ok": False, "reply": result.get("error", "agent error")}
    return {"ok": True, "reply": result.get("reply", "")}


def session_path(project_path: str | Path, session_id: str) -> Path:
    """Convenience for callers that want the on-disk manifest path
    without going through [`load_manifest`]."""
    return manifests_dir(Path(project_path)) / f"{session_id}.json"


__all__ = [
    "DEFAULT_MANAGER",
    "SessionManager",
    "SessionManifest",
    "claude_sdk_available",
    "list_manifests",
    "load_manifest",
    "manifests_dir",
    "most_recent_session_id",
    "run_session",
    "session_path",
    "touch_manifest",
    "write_manifest",
]
