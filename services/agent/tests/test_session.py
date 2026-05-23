"""M1-P-07 acceptance tests for per-project session resume.

The full e2e ("close tab, reopen, chat history intact") is the
M1-Q-03 Playwright spec; this file pins the persistence + resume
plumbing via injected fake clients so the suite stays fast and runs
without `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent.session import (
    SessionManager,
    SessionManifest,
    list_manifests,
    load_manifest,
    most_recent_session_id,
    touch_manifest,
    write_manifest,
)


def test_write_and_load_manifest_round_trip(tmp_path: Path) -> None:
    m = SessionManifest(
        project_id="p1",
        session_id="s1",
        project_path=str(tmp_path),
    )
    path = write_manifest(m)
    assert path == tmp_path / ".kiclaude" / "sessions" / "s1.json"
    assert path.is_file()
    back = load_manifest(tmp_path, "s1")
    assert back is not None
    assert back.project_id == "p1"
    assert back.session_id == "s1"


def test_load_manifest_missing_returns_none(tmp_path: Path) -> None:
    assert load_manifest(tmp_path, "nope") is None


def test_load_manifest_malformed_returns_none(tmp_path: Path) -> None:
    target = tmp_path / ".kiclaude" / "sessions"
    target.mkdir(parents=True)
    (target / "bad.json").write_text("{not-json")
    assert load_manifest(tmp_path, "bad") is None


def test_list_manifests_sorted_by_last_seen_desc(tmp_path: Path) -> None:
    write_manifest(
        SessionManifest(
            project_id="p",
            session_id="older",
            project_path=str(tmp_path),
            last_seen_at_unix=10.0,
        )
    )
    write_manifest(
        SessionManifest(
            project_id="p",
            session_id="newer",
            project_path=str(tmp_path),
            last_seen_at_unix=20.0,
        )
    )
    ms = list_manifests(tmp_path)
    assert [m.session_id for m in ms] == ["newer", "older"]


def test_most_recent_session_id(tmp_path: Path) -> None:
    assert most_recent_session_id(tmp_path) is None
    write_manifest(
        SessionManifest(
            project_id="p",
            session_id="s",
            project_path=str(tmp_path),
        )
    )
    assert most_recent_session_id(tmp_path) == "s"


def test_touch_manifest_bumps_last_seen(tmp_path: Path) -> None:
    write_manifest(
        SessionManifest(
            project_id="p",
            session_id="s",
            project_path=str(tmp_path),
            last_seen_at_unix=10.0,
        )
    )
    updated = touch_manifest(tmp_path, "s")
    assert updated is not None
    assert updated.last_seen_at_unix > 10.0


# ----------------------------------------------------------------
# SessionManager with injected fake client.
# ----------------------------------------------------------------


class _FakeInitMessage:
    """Mimics `SystemMessage(subtype="init")` from the SDK."""

    def __init__(self, session_id: str) -> None:
        self.subtype = "init"
        self.session_id = session_id


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]


class _FakeClient:
    """Records the captured prompt + the resume_session_id it was
    constructed with, and streams an init frame + an assistant reply."""

    def __init__(self, session_id: str, resume_session_id: str | None) -> None:
        self.session_id = session_id
        self.resume_session_id = resume_session_id
        self.prompts: list[str] = []
        self.closed = False

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self):
        yield _FakeInitMessage(self.session_id)
        yield _FakeAssistantMessage(f"echo: {self.prompts[-1]}")

    async def close(self) -> None:
        self.closed = True


def _factory_returning(session_id: str):
    async def factory(*, project_path: Path, resume_session_id: str | None) -> Any:
        return _FakeClient(session_id, resume_session_id)
    return factory


def _patched_assistant_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the AssistantMessage/TextBlock isinstance check in
    `session._collect_assistant_text` so our fakes count."""
    import agent.session as session_mod

    def collect_text(msg: Any) -> list[str]:
        if isinstance(msg, _FakeAssistantMessage):
            return [block.text for block in msg.content if isinstance(block, _FakeTextBlock)]
        return []

    monkeypatch.setattr(session_mod, "_collect_assistant_text", collect_text)


@pytest.mark.asyncio
async def test_chat_captures_init_session_id_and_persists_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched_assistant_classes(monkeypatch)
    mgr = SessionManager()
    result = await mgr.chat(
        "proj-1",
        tmp_path,
        "hello",
        client_factory=_factory_returning("sess-001"),
    )
    assert result["ok"] is True
    assert result["session_id"] == "sess-001"
    assert result["reply"] == "echo: hello"
    manifest = load_manifest(tmp_path, "sess-001")
    assert manifest is not None
    assert manifest.project_id == "proj-1"


@pytest.mark.asyncio
async def test_chat_reuses_persisted_session_on_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched_assistant_classes(monkeypatch)
    # Pre-seed the disk with a session manifest.
    write_manifest(
        SessionManifest(
            project_id="proj-1",
            session_id="sess-prior",
            project_path=str(tmp_path),
        )
    )
    seen_resume: list[str | None] = []

    async def factory(*, project_path: Path, resume_session_id: str | None) -> Any:
        seen_resume.append(resume_session_id)
        return _FakeClient("sess-prior", resume_session_id)

    mgr = SessionManager()
    result = await mgr.chat("proj-1", tmp_path, "hello again", client_factory=factory)
    assert result["ok"] is True
    assert result["session_id"] == "sess-prior"
    assert seen_resume == ["sess-prior"], (
        "factory must receive the persisted session_id as resume hint"
    )


@pytest.mark.asyncio
async def test_chat_reuses_in_memory_client_on_second_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched_assistant_classes(monkeypatch)
    constructed: list[_FakeClient] = []

    async def factory(*, project_path: Path, resume_session_id: str | None) -> Any:
        c = _FakeClient("sess-A", resume_session_id)
        constructed.append(c)
        return c

    mgr = SessionManager()
    await mgr.chat("proj-1", tmp_path, "one", client_factory=factory)
    await mgr.chat("proj-1", tmp_path, "two", client_factory=factory)
    assert len(constructed) == 1, "client should be reused across turns"
    assert constructed[0].prompts == ["one", "two"]


@pytest.mark.asyncio
async def test_close_releases_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched_assistant_classes(monkeypatch)
    constructed: list[_FakeClient] = []

    async def factory(*, project_path: Path, resume_session_id: str | None) -> Any:
        c = _FakeClient("sess-Q", resume_session_id)
        constructed.append(c)
        return c

    mgr = SessionManager()
    await mgr.chat("proj-1", tmp_path, "hi", client_factory=factory)
    await mgr.close("proj-1")
    assert constructed[0].closed is True
    assert mgr.entry("proj-1") is None


@pytest.mark.asyncio
async def test_chat_surfaces_factory_error_in_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched_assistant_classes(monkeypatch)

    class _ExplodingClient:
        async def query(self, _prompt: str) -> None:
            raise RuntimeError("agent crashed")

        async def receive_response(self):
            if False:
                yield None

    async def factory(*, project_path: Path, resume_session_id: str | None) -> Any:
        return _ExplodingClient()

    mgr = SessionManager()
    result = await mgr.chat("proj-1", tmp_path, "x", client_factory=factory)
    assert result["ok"] is False
    assert "agent crashed" in result["error"]


def test_manifest_path_is_under_project_dot_kiclaude(tmp_path: Path) -> None:
    """The persistence location matches the SPEC §6.4.2 layout."""
    m = SessionManifest(
        project_id="p",
        session_id="abc",
        project_path=str(tmp_path),
    )
    path = write_manifest(m)
    assert str(path).startswith(str(tmp_path / ".kiclaude" / "sessions"))
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == 1
    assert payload["session_id"] == "abc"
