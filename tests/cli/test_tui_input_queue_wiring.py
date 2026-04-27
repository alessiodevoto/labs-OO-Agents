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


def test_tui_agent_uses_cached_block_formatter_by_default():
    """``BaseTUIAgent`` wires ``CachedBlockFormatter`` so immutable
    blocks (system prompt, doc(self), anything set with
    ``immutable=True``) land in a stable SYSTEM prefix the provider
    can cache across turns.
    """
    from context_blocks.renderers import CachedBlockFormatter

    agent = _fresh_agent()
    assert isinstance(agent.render_config.block_formatter, CachedBlockFormatter)


def test_tui_agent_caller_can_override_render_config():
    """Explicit ``render_config=`` wins over the CachedBlockFormatter default."""
    from context_blocks.formatter import XMLBlockFormatter
    from context_blocks.render_config import RenderConfig
    from unifiedllm import FakeLLMClient

    explicit = RenderConfig(block_formatter=XMLBlockFormatter())
    agent = TUIAgent(llm=FakeLLMClient(), render_config=explicit)
    assert isinstance(agent.render_config.block_formatter, XMLBlockFormatter)


# ---------------------------------------------------------------------------
# Queue status is surfaced through a dynamic context block
# ---------------------------------------------------------------------------


def test_queue_status_dynamic_block_is_registered():
    """``BaseTUIAgent`` registers ``queue_status`` as a dynamic context
    block so the LLM sees pending counts every turn, in place of the
    per-put Notification events we used to emit."""
    from context_blocks.models import DynamicContext

    agent = _fresh_agent()
    entry = dict(agent.context_manager._raw_items()).get("queue_status")
    assert isinstance(entry, DynamicContext)
    assert entry.expr == "self._queue_status(max_items=3, max_chars=80)"


def test_queue_status_empty_when_all_queues_drained():
    agent = _fresh_agent()
    # No pending items → empty string (keeps the rendered block quiet).
    assert agent._queue_status() == ""


def test_queue_status_lists_pending_per_queue():
    agent = _fresh_agent()
    agent._user_messages_in.put("a")
    agent._user_messages_in.put("b")
    # Also add a second queue to verify multi-queue formatting.
    agent._jobs_in = InputQueue("job_outputs", agent=agent)
    agent._jobs_in.put({"id": 1})

    status = agent._queue_status()
    # One line per non-empty queue, stable enough to assert substrings.
    assert "user_messages: 2 pending" in status
    assert "job_outputs: 1 pending" in status


def test_queue_status_skips_empty_queues():
    agent = _fresh_agent()
    agent._jobs_in = InputQueue("job_outputs", agent=agent)
    agent._user_messages_in.put("only-this-one")

    status = agent._queue_status()
    assert "user_messages" in status
    assert "job_outputs" not in status


def test_queue_status_composes_each_non_empty_queues_status():
    """``_queue_status`` delegates the per-queue rendering to each
    queue's ``status()`` and joins the non-empty ones. Detailed
    formatting (numbered items, overflow, newline flattening, non-str
    preview) is covered in test_input_queue.py — this test just
    verifies composition."""
    agent = _fresh_agent()
    agent._user_messages_in.put("hi")
    agent._jobs_in = InputQueue("job_outputs", agent=agent)
    agent._jobs_in.put({"id": 1})
    agent._idle_in = InputQueue("idle", agent=agent)  # stays empty → dropped

    status = agent._queue_status(max_items=3, max_chars=80)
    assert agent._user_messages_in.status(max_items=3, max_chars=80) in status
    assert agent._jobs_in.status(max_items=3, max_chars=80) in status
    assert "idle" not in status


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
    agent._user_messages_in.put("hi")
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
# submit_message merge semantics (no events — just deque shape)
# ---------------------------------------------------------------------------


def test_three_consecutive_submits_while_busy_merge_into_one_item():
    """Three Enters typed while the dispatcher is busy compose a single
    multi-line queue item, separated by ``\\n``.
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
        assert agent._user_messages_in.snapshot() == ["two\nthree\nfour"]
        proceed.set()
        await app._agent_task

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


def test_is_thinking_true_during_mid_turn_drain():
    """When the agent's ``respond()`` does ``await self.user_messages.get()``
    mid-turn (clarification flow), the queue has a waiter — but the
    agent is still thinking, not idle. The dispatcher's ``_in_respond``
    flag must report True for the duration.

    Regression guard for the bug where ``is_thinking()`` consulted
    ``has_waiters()`` and returned False during mid-turn drain, dropping
    the spinner while the agent was genuinely working.
    """

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    in_drain = asyncio.Event()
    proceed = asyncio.Event()

    async def _respond(notification, restored=None):
        # Mid-turn drain: agent waits for the next user message.
        in_drain.set()
        await proceed.wait()
        # Pull from the LLM-facing facade — same path the agent would
        # use in ``execute_python``.
        await agent.user_messages.get()
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("first")
        await asyncio.wait_for(in_drain.wait(), timeout=0.5)
        # Inside respond, before the mid-turn get() is even reached.
        assert app.is_thinking() is True
        # Now release respond — it'll await user_messages.get(), which
        # registers a waiter on the queue. Submit a second message and
        # confirm is_thinking stays True throughout.
        proceed.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if agent._user_messages_in.has_waiters():
                break
        assert agent._user_messages_in.has_waiters(), (
            "agent should be suspended on user_messages.get()"
        )
        # The bug under regression: previously is_thinking() returned
        # False here because has_waiters() was True.
        assert app.is_thinking() is True
        app.submit_message("answer")
        await app._agent_task

    asyncio.run(_run())


def test_dispatcher_does_not_call_on_get_hook_directly():
    """Locks the docstring-stated invariant: the dispatcher loop does
    NOT call the InputQueue's ``on_get`` hook itself; it relies on the
    hook firing from inside ``get()``. A future refactor that "helps"
    by also calling the hook from the loop would double-fire the
    user-bar render and the ``TUIUserInput`` log.
    """

    agent = _fresh_agent()
    app = TUIApplication(agent=agent)

    fires: list[str] = []
    agent._user_messages_in.set_on_get(fires.append)

    async def _respond(notification, restored=None):
        return _DispatchResult(kind="STOP")

    agent.respond = _respond  # type: ignore[method-assign]

    async def _run():
        app.submit_message("once")
        await app._agent_task
        # Exactly one fire — through get(), not double-fired by the loop.
        assert fires == ["once"]

    asyncio.run(_run())


def test_coalesce_string_into_queue_non_string_tail_preserves_both():
    """When the trailing queued item is non-string,
    ``_coalesce_string_into_queue`` must not merge — it pushes the
    non-string tail back ahead of the new string. Tests the helper
    directly (no dispatcher start) so we exercise the non-string
    branch without needing an event loop.
    """
    from nemo_oo_agents.runtime.input_queue import InputQueue
    from nemo_oo_agents_cli.tui.tui_application import _coalesce_string_into_queue

    inq: InputQueue[Any] = InputQueue("user_messages")
    inq.put({"job_id": 7})
    _coalesce_string_into_queue(inq, "hi")

    assert inq.snapshot() == [{"job_id": 7}, "hi"]


def test_coalesce_string_into_queue_string_tail_merges():
    """String tail → coalesce with newline separator (the typical UX
    case: user types, Enter, types more, Enter while busy)."""
    from nemo_oo_agents.runtime.input_queue import InputQueue
    from nemo_oo_agents_cli.tui.tui_application import _coalesce_string_into_queue

    inq: InputQueue[str] = InputQueue("user_messages")
    inq.put("first line")
    _coalesce_string_into_queue(inq, "second line")

    assert inq.snapshot() == ["first line\nsecond line"]


def test_coalesce_string_into_queue_empty_queue():
    """Empty queue → just put the new string."""
    from nemo_oo_agents.runtime.input_queue import InputQueue
    from nemo_oo_agents_cli.tui.tui_application import _coalesce_string_into_queue

    inq: InputQueue[str] = InputQueue("user_messages")
    _coalesce_string_into_queue(inq, "alone")
    assert inq.snapshot() == ["alone"]


def test_submit_message_dropped_when_agent_lacks_queue():
    """Defensive branch when the agent has no ``_user_messages_in``:
    emit a diagnostic block and start no dispatcher. Locks the
    diagnostic message so refactors don't silently drop it.
    """

    class _BareAgent:
        pass

    app = TUIApplication(agent=_BareAgent())
    blocks: list[str] = []
    app.emit_block = blocks.append  # type: ignore[method-assign]

    app.submit_message("hi")

    assert any("submit_message dropped" in b for b in blocks)
    assert app._agent_task is None
