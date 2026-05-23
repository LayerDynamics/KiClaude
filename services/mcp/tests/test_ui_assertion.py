"""M1-P-05 boot-time assertion: UI-only tools never reach Claude.

Pins the SPEC §1.4 #4 invariant — if a refactor accidentally adds a
`ui_*` name to `_CLAUDE_TOOLS`, `build_server` aborts with a clear
`RuntimeError` rather than silently leaking raw coordinates to the
agent.
"""

from __future__ import annotations

import pytest
from kc_mcp.server import (
    _CLAUDE_TOOLS,
    assert_no_ui_tools_in_claude_registry,
    build_server,
)


def test_claude_registry_has_no_ui_tools_today() -> None:
    """Self-check: every entry in `_CLAUDE_TOOLS` is `kc_*` only."""
    for tool_obj in _CLAUDE_TOOLS:
        name = getattr(tool_obj, "name", "")
        assert isinstance(name, str)
        assert not name.startswith("ui_"), f"{name} leaked into Claude registry"


def test_assert_helper_passes_for_clean_list() -> None:
    # No exception when the list is clean.
    assert_no_ui_tools_in_claude_registry(_CLAUDE_TOOLS)


def test_assert_helper_fails_when_ui_tool_added() -> None:
    """Drop a fake `ui_*` callable into the list copy and confirm
    the guard fires."""

    class FakeUi:
        name = "ui_drag_drop_things"

    bad_list = [*_CLAUDE_TOOLS, FakeUi()]
    with pytest.raises(RuntimeError, match="ui_drag_drop_things"):
        assert_no_ui_tools_in_claude_registry(bad_list)


def test_build_server_runs_the_assertion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap `_CLAUDE_TOOLS` with a tainted list and verify
    `build_server` refuses to construct the config."""
    import kc_mcp.server as server_mod

    class FakeUi:
        name = "ui_anything"

    monkeypatch.setattr(server_mod, "_CLAUDE_TOOLS", [FakeUi()])
    with pytest.raises(RuntimeError):
        build_server()
