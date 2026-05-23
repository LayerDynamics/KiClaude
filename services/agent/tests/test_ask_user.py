"""M3-P-08 — AskUserQuestion bridge.

Two layers of tests:
- registry semantics: dispatch_question round-trip, cancellation,
  unknown-id rejection, payload validation.
- FastAPI endpoint contract: pending / answer / cancel routes
  emit the exact JSON shape the WS gateway proxies to the React
  chat sidebar (`AskUserQuestionCard`)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from agent import ask_user
from agent.main import app


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Each test starts with an empty registry — leaked Futures
    from prior tests must not pollute the queue."""
    ask_user.reset_registry_for_tests()
    yield
    ask_user.reset_registry_for_tests()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------
# Registry-level tests.
# ---------------------------------------------------------------------


async def test_dispatch_resolves_when_answer_arrives() -> None:
    registry = ask_user.registry()
    options = [{"label": "yes"}, {"label": "no"}]

    async def answer_after_a_beat() -> None:
        # Wait until the question is queued, then answer the only one.
        await asyncio.sleep(0)
        pending = await registry.pending()
        assert len(pending) == 1
        await registry.answer(
            pending[0]["id"],
            ask_user.AskUserAnswer(picks=["yes"], notes="confirmed"),
        )

    answer_task = asyncio.create_task(answer_after_a_beat())
    answer = await registry.dispatch_question(
        "Proceed?",
        options,
        header="Confirm",
    )
    await answer_task
    assert answer.picks == ["yes"]
    assert answer.notes == "confirmed"


async def test_dispatch_payload_serialises_camel_case_for_react() -> None:
    """The React side reads `multiSelect`, not `multi_select` — the
    payload's `to_dict()` must emit the camel-case shape the WS
    protocol expects."""

    async def capture_payload() -> dict:
        await asyncio.sleep(0)
        pending = await ask_user.registry().pending()
        # Answer immediately so the dispatch returns.
        await ask_user.registry().answer(
            pending[0]["id"], ask_user.AskUserAnswer(picks=["a"])
        )
        return pending[0]

    task = asyncio.create_task(capture_payload())
    await ask_user.registry().dispatch_question(
        "Pick all that apply",
        [{"label": "a"}, {"label": "b"}],
        multi_select=True,
        project_id="blinky",
    )
    payload = await task
    assert payload["multiSelect"] is True
    assert payload["projectId"] == "blinky"
    assert payload["question"] == "Pick all that apply"
    assert payload["options"] == [{"label": "a"}, {"label": "b"}]


async def test_empty_options_rejected() -> None:
    with pytest.raises(ValueError, match="at least one option"):
        await ask_user.registry().dispatch_question("any?", [])


async def test_label_missing_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty `label`"):
        await ask_user.registry().dispatch_question(
            "pick", [{"description": "missing the label key"}]
        )


async def test_unknown_answer_returns_false() -> None:
    """Late answers — when the dispatcher cancelled before the
    user clicked Submit — must be silently dropped, never crash."""
    ok = await ask_user.registry().answer(
        "does-not-exist",
        ask_user.AskUserAnswer(picks=["x"]),
    )
    assert ok is False


async def test_cancel_unblocks_awaiter() -> None:
    """Pulling the question out from under the dispatcher must
    raise CancelledError, not deadlock the test."""
    options = [{"label": "yes"}]

    async def cancel_after_a_beat() -> None:
        await asyncio.sleep(0)
        pending = await ask_user.registry().pending()
        assert len(pending) == 1
        await ask_user.registry().cancel(pending[0]["id"])

    cancel_task = asyncio.create_task(cancel_after_a_beat())
    with pytest.raises(asyncio.CancelledError):
        await ask_user.registry().dispatch_question("any?", options)
    await cancel_task


async def test_dispatch_two_questions_queues_both_oldest_first() -> None:
    registry = ask_user.registry()

    async def dispatch(q: str, label: str) -> ask_user.AskUserAnswer:
        return await registry.dispatch_question(q, [{"label": label}])

    a_task = asyncio.create_task(dispatch("Q1?", "a"))
    await asyncio.sleep(0)
    b_task = asyncio.create_task(dispatch("Q2?", "b"))
    await asyncio.sleep(0)

    pending = await registry.pending()
    assert len(pending) == 2
    assert pending[0]["question"] == "Q1?"
    assert pending[1]["question"] == "Q2?"

    await registry.answer(pending[0]["id"], ask_user.AskUserAnswer(picks=["a"]))
    await registry.answer(pending[1]["id"], ask_user.AskUserAnswer(picks=["b"]))
    assert (await a_task).picks == ["a"]
    assert (await b_task).picks == ["b"]


# ---------------------------------------------------------------------
# FastAPI endpoint tests.
# ---------------------------------------------------------------------


async def test_endpoints_round_trip_a_question(client: TestClient) -> None:
    """End-to-end through the gateway-facing endpoints: dispatch
    queues the question, GET /pending returns it, POST /answer
    unblocks the dispatcher."""

    async def dispatch_then_assert() -> ask_user.AskUserAnswer:
        return await ask_user.registry().dispatch_question(
            "Continue?",
            [{"label": "yes"}, {"label": "no"}],
            header="Confirm",
        )

    dispatch_task = asyncio.create_task(dispatch_then_assert())
    await asyncio.sleep(0)  # let the dispatch queue first

    resp = client.get("/ask-user/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["questions"]) == 1
    qid = body["questions"][0]["id"]
    assert body["questions"][0]["question"] == "Continue?"
    assert body["questions"][0]["header"] == "Confirm"

    resp = client.post(
        f"/ask-user/{qid}/answer",
        json={"picks": ["yes"], "notes": ""},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    answer = await dispatch_task
    assert answer.picks == ["yes"]


def test_answer_unknown_id_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/ask-user/does-not-exist/answer",
        json={"picks": ["x"]},
    )
    assert resp.status_code == 404
    assert "does-not-exist" in resp.json()["detail"]


def test_cancel_unknown_id_returns_404(client: TestClient) -> None:
    resp = client.delete("/ask-user/does-not-exist")
    assert resp.status_code == 404


async def test_endpoint_cancel_unblocks_awaiter(client: TestClient) -> None:
    """The DELETE endpoint must propagate to the registry's
    cancel() — pinned by an actual dispatch + DELETE round-trip."""

    async def dispatch() -> None:
        with pytest.raises(asyncio.CancelledError):
            await ask_user.registry().dispatch_question(
                "blocked?", [{"label": "ok"}]
            )

    task = asyncio.create_task(dispatch())
    await asyncio.sleep(0)
    pending = client.get("/ask-user/pending").json()["questions"]
    assert len(pending) == 1
    qid = pending[0]["id"]

    resp = client.delete(f"/ask-user/{qid}")
    assert resp.status_code == 200
    await task
