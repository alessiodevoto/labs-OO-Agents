# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration-ish tests for the per-turn TUI respond() dispatcher.

These don't spin up a real LLM — they verify the plumbing:
- ``TUIAgent`` has ``_user_messages_in`` (hidden InputQueue) and
  ``user_messages`` (LLM-facing OutputQueue facade) both wired up.
- ``TUIApplication.submit_message`` pushes onto the InputQueue and
  lazy-starts the dispatcher; subsequent pushes flow through without
  restarting.
- The dispatcher reads ``RespondResult.kind`` and dispatches accordingly
  (GET_USER_INPUT → await user_messages_in; WAIT → race queues; STOP → exit).
- The dispatcher passes ``restored`` into successive ``respond()`` calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nemo_oo_agents import InputQueue
from nemo_oo_agents.events import Notification
from nemo_oo_agents.runtime.input_queue import OutputQueue
from nemo_oo_agents_cli.tui.agent import TUIAgent
from nemo_oo_agents_cli.tui.tui_application import TUIApplication


def _fresh_agent() -> TUIAgent:
    """TUIAgent with a fake LLM so we don't need API keys."""
    from unifiedllm import FakeLLMClient

    return TUIAgent(llm=FakeLLMClient())


# ---------------------------------------------------------------------------
# Agent-side: queue declaration
# ---------------------------------------------------------------------------


def test_tui_agent_has_input_and_output_queue_for_user_messages():
    agent = _fresh_agent()
    # Hidden InputQueue with the full producer/dispatcher API.
    assert isinstance(agent._user_messages_in, InputQueue)
    assert agent._user_messages_in.name == "user_messages"
    # LLM-facing OutputQueue facade — same name, just .get() / .name.
    assert isinstance(agent.user_messages, OutputQueue)
    assert agent.user_messages.name == "user_messages"


def test_user_messages_queue_is_hidden_from_doc():
    """The hidden InputQueue must NOT appear in the LLM's API listing."""
    from nemo_oo_agents.agentdoc import doc

    agent = _fresh_agent()
    api = doc(agent)
    assert "_user_messages_in" not in api
    # The OutputQueue facade IS visible — that's the LLM-facing handle.
    assert "user_messages" in api


def test_pushing_emits_notification_to_event_manager():
    agent = _fresh_agent()
    agent._user_messages_in.put("hello")
    notifications = [
        ev for ev in agent.event_manager._backend.all_events() if isinstance(ev, Notification)
    ]
    assert len(notifications) == 1
    assert notifications[0].source == "queue:user_messages"
    assert "1 item" in notifications[0].description


# ---------------------------------------------------------------------------
# OutputQueue facade
# ---------------------------------------------------------------------------


def test_output_queue_exposes_only_get_and_name():
    """OutputQueue's public surface is intentionally tiny."""
    agent = _fresh_agent()
    out = agent.user_messages
    public = {n for n in dir(out) if not n.startswith("_")}
    assert "get" in public
    assert "name" in public
    # Producer / dispatcher methods stay on the InputQueue, not here.
    for forbidden in ("put", "snapshot", "qsize", "pop_last", "has_waiters"):
        assert forbidden not in public


async def _await_get_via_facade():
    agent = _fresh_agent()
    agent._user_messages_in.put("hi", emit_notification=False)
    msg = await agent.user_messages.get()
    return msg


def test_output_queue_get_delegates_to_input_queue():
    assert asyncio.run(_await_get_via_facade()) == "hi"


# ---------------------------------------------------------------------------
# TUIApplication dispatcher integration (no actual run_async)
# ---------------------------------------------------------------------------


class _DispatchResult:
    """Minimal duck of ``RespondResult`` — the dispatcher reads .kind / .persist."""

    def __init__(self, kind: str, persist: dict[str, Any] | None = None) -> None:
        self.kind = kind
        self.persist = persist or {}


def test_submit_message_pushes_to_queue_and_starts_dispatcher():
    """``submit_message`` pushes onto the agent's InputQueue; the
    dispatcher lazy-starts, consumes the message, and calls
    ``respond((name, item))``."""

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    calls: list[tuple[tuple[str, Any], dict[str, Any] | None]] = []

    async def _respond(notification, restored=None):
        calls.append((notification, restored))
        # Immediately STOP so the dispatcher exits cleanly after one turn.
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    assert agent._user_messages_in.qsize() == 0
    assert app._agent_task is None

    async def _run():
        app.submit_message("hello")
        assert agent._user_messages_in.snapshot() == ["hello"]
        assert app._agent_task is not None
        await app._agent_task
        assert len(calls) == 1
        assert calls[0][0] == ("user_messages", "hello")
        assert calls[0][1] is None

    asyncio.run(_run())


def test_second_message_does_not_spawn_second_dispatcher():
    """While the dispatcher task is alive, additional pushes just go
    onto the queue — no parallel respond() task."""

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def _respond(notification, restored=None):
        started.set()
        await proceed.wait()
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(started.wait(), timeout=0.5)
        first_task = app._agent_task
        assert first_task is not None and not first_task.done()

        app.submit_message("second")
        assert agent._user_messages_in.snapshot() == ["second"]
        assert app._agent_task is first_task

        proceed.set()
        await first_task

    asyncio.run(_run())


def test_dispatcher_forwards_persist_as_restored_next_turn():
    """When respond() returns persist={"x": 1}, the next respond()
    call should receive restored={"x": 1}."""

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    calls: list[tuple[tuple[str, Any], dict[str, Any] | None]] = []

    async def _respond(notification, restored=None):
        calls.append((notification, restored))
        if len(calls) == 1:
            return _DispatchResult(kind="GET_USER_INPUT", persist={"cursor": 42})
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("hi")
        await asyncio.sleep(0.05)
        app.submit_message("again")
        await app._agent_task
        assert len(calls) == 2
        assert calls[0] == (("user_messages", "hi"), None)
        assert calls[1] == (("user_messages", "again"), {"cursor": 42})

    asyncio.run(_run())


def test_dispatcher_wait_kind_races_all_declared_input_queues():
    """kind="WAIT" should race every ``InputQueue`` attribute on the
    agent (hidden ones included) and re-enter respond() with whichever
    fires first."""

    agent = _fresh_agent()
    # Subclass-style: declare a second queue pair as instance attrs.
    agent._jobs_in = InputQueue("job_outputs", agent=agent)
    agent.job_outputs = agent._jobs_in.reader
    app = TUIApplication(agent=agent)

    calls: list[tuple[str, Any]] = []

    async def _respond(notification, restored=None):
        calls.append(notification)
        if len(calls) == 1:
            return _DispatchResult(kind="WAIT")
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("first")
        for _ in range(5):
            await asyncio.sleep(0)
        agent._jobs_in.put({"id": 7})
        await app._agent_task
        assert calls[0] == ("user_messages", "first")
        assert calls[1] == ("job_outputs", {"id": 7})

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Notification semantics end-to-end through TUIApplication
# ---------------------------------------------------------------------------


def _count_notifications(agent: TUIAgent) -> int:
    return sum(
        1 for ev in agent.event_manager._backend.all_events() if isinstance(ev, Notification)
    )


def test_three_consecutive_submits_while_busy_emit_one_notification():
    """Three Enters typed while the dispatcher is busy compose a single
    multi-line queue item AND emit exactly one Notification (the first
    Enter's). Subsequent merges inherit the tag, no new event fires.
    """
    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    proceed = asyncio.Event()

    async def _respond(notification, restored=None):
        # Block forever so the dispatcher stays inside respond() and
        # subsequent submit_messages all hit the slow-merge path.
        await proceed.wait()
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("one")
        # Let the dispatcher start and consume "one" so we KNOW the
        # subsequent submits land on a fresh queue (not the fast path).
        for _ in range(20):
            await asyncio.sleep(0)
            if app.is_thinking():
                break
        # Three more Enters while respond is blocked → all merge.
        app.submit_message("two")
        app.submit_message("three")
        app.submit_message("four")
        # Queue holds one merged item.
        assert agent._user_messages_in.snapshot() == ["two\nthree\nfour"]
        # Notifications: one for "one" (delivered to the dispatcher's
        # awaited get()? no — dispatcher hadn't started yet; "one" was
        # queued and emitted), one for "two" (slow path, queue was
        # empty after "one" was consumed). "three" and "four" merge
        # into "two"'s slot, inheriting its tag — no new notification.
        assert _count_notifications(agent) == 2
        proceed.set()
        await app._agent_task

    asyncio.run(_run())


def test_pop_last_retracts_notification_at_agent_level():
    """The Up-arrow un-queue path: pop_last on the hidden InputQueue
    must remove the previously emitted Notification from the agent's
    event log so the LLM doesn't see "1 item pending" for an item the
    user pulled back into the input buffer.
    """
    agent = _fresh_agent()
    # Push directly to the InputQueue (mirrors what submit_message
    # does) — no need to spin the dispatcher just to test retraction.
    agent._user_messages_in.put("editing-this")
    assert _count_notifications(agent) == 1
    assert agent._user_messages_in.snapshot() == ["editing-this"]

    # User presses Up arrow — the app's _pop_last_queued closure
    # ultimately calls inq.pop_last(), which retracts the tag.
    popped = agent._user_messages_in.pop_last()
    assert popped == "editing-this"
    assert _count_notifications(agent) == 0


def test_direct_delivery_via_dispatcher_skips_notification():
    """When the dispatcher is between turns awaiting ``get()`` and a
    new message is submitted, ``put()`` hands it directly to the
    waiter — and skips emitting a Notification (the await's return
    value IS the notification).
    """
    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    seen_notifications_per_turn: list[int] = []

    async def _respond(notification, restored=None):
        # Snapshot count when the dispatcher hands us a message.
        seen_notifications_per_turn.append(_count_notifications(agent))
        if len(seen_notifications_per_turn) >= 2:
            return _DispatchResult(kind="STOP")
        return _DispatchResult(kind="GET_USER_INPUT")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        # Push #1: dispatcher hasn't started yet → queues → fires Notification.
        app.submit_message("first")
        # Wait until the dispatcher consumed "first" and is awaiting
        # the next message via get() (has_waiters becomes True).
        for _ in range(50):
            await asyncio.sleep(0)
            if agent._user_messages_in.has_waiters():
                break
        assert agent._user_messages_in.has_waiters()
        before = _count_notifications(agent)
        # Push #2: directly delivered to the waiter — no Notification.
        app.submit_message("second")
        await app._agent_task
        after = _count_notifications(agent)
        assert after == before, (
            f"direct-delivery should not emit a Notification (before={before}, after={after})"
        )

    asyncio.run(_run())


def test_dispatcher_get_keeps_notification_in_history():
    """When the dispatcher pumps a queued item via ``get()``, the
    Notification stays in the event log — it's real history (the
    item arrived and was processed). Only ``pop_last`` retracts.
    """
    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    async def _respond(notification, restored=None):
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("hello")
        await app._agent_task
        # Queue is now empty (dispatcher pumped it), but the
        # Notification we emitted on submit is still in history.
        assert agent._user_messages_in.qsize() == 0
        assert _count_notifications(agent) == 1

    asyncio.run(_run())


def test_is_thinking_false_when_dispatcher_blocked_on_queue():
    """Between turns the dispatcher is awaiting ``_user_messages_in.get()``.
    During that await ``has_waiters()`` is True and ``is_thinking()``
    returns False — the agent is idle, not generating."""

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    async def _respond(notification, restored=None):
        return _DispatchResult(kind="GET_USER_INPUT")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("hello")
        for _ in range(20):
            await asyncio.sleep(0)
            if agent._user_messages_in.has_waiters():
                break
        assert agent._user_messages_in.has_waiters()
        assert app.is_thinking() is False
        app._agent_task.cancel()
        try:
            await app._agent_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
