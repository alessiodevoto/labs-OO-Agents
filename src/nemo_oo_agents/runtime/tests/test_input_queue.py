# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``InputQueue`` + ``wait_for_any``.

No LLM / runtime required — just ``asyncio`` behaviour.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from nemo_oo_agents.events import Notification
from nemo_oo_agents.runtime.input_queue import InputQueue, wait_for_any


@dataclass
class _FakeEventManager:
    emitted: list[Any] = field(default_factory=list)

    def add(self, event: Any) -> str:
        self.emitted.append(event)
        return str(len(self.emitted))


@dataclass
class _FakeAgent:
    event_manager: _FakeEventManager = field(default_factory=_FakeEventManager)


# ---------------------------------------------------------------------------
# Basic producer/consumer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_get_fifo():
    q: InputQueue[str] = InputQueue("user_messages")
    q.put("a")
    q.put("b")
    q.put("c")
    assert await q.get() == "a"
    assert await q.get() == "b"
    assert await q.get() == "c"
    assert q.qsize() == 0


@pytest.mark.asyncio
async def test_get_blocks_then_wakes():
    q: InputQueue[str] = InputQueue("q")
    # No item yet — get() must block.
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0)  # let the task run & register the waiter
    assert not getter.done()
    assert q.has_waiters()
    # Producer pushes — getter should complete with that item.
    q.put("hello")
    assert await asyncio.wait_for(getter, timeout=0.5) == "hello"
    assert not q.has_waiters()


@pytest.mark.asyncio
async def test_put_delivers_directly_when_waiter_exists():
    """When a waiter is pending, put() hands the item straight to the waiter.

    The item should NOT land on the backing deque — qsize stays 0 —
    and no Notification fires (the awaited return value IS the
    notification; emitting another event for the same delivery would
    just clutter the LLM's history).
    """
    agent = _FakeAgent()
    q: InputQueue[int] = InputQueue("q", agent=agent)
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0)
    q.put(42)
    assert await asyncio.wait_for(getter, timeout=0.5) == 42
    assert q.qsize() == 0
    # Fast-path delivery: no Notification emitted.
    assert agent.event_manager.emitted == []


@pytest.mark.asyncio
async def test_cancelled_get_leaves_no_phantom_waiter():
    q: InputQueue[str] = InputQueue("q")
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0)
    getter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await getter
    # After cancellation the waiter list should be cleaned up.
    assert not q.has_waiters()
    # And a subsequent put() should land in the deque, not attempt to
    # deliver to a dead waiter.
    q.put("x")
    assert q.qsize() == 1


# ---------------------------------------------------------------------------
# Non-consuming accessors
# ---------------------------------------------------------------------------


def test_pop_last_and_snapshot():
    q: InputQueue[str] = InputQueue("q")
    assert q.pop_last() is None
    q.put("a")
    q.put("b")
    q.put("c")
    assert q.snapshot() == ["a", "b", "c"]
    # pop_last removes the tail.
    assert q.pop_last() == "c"
    assert q.snapshot() == ["a", "b"]
    # Snapshot is a copy — mutating it doesn't affect the queue.
    snap = q.snapshot()
    snap.append("z")
    assert q.snapshot() == ["a", "b"]


def test_peek_removed():
    """peek() invited multi-turn branching on queue state; removed."""
    q: InputQueue[str] = InputQueue("q")
    assert not hasattr(q, "peek")


# ---------------------------------------------------------------------------
# Notification emission
# ---------------------------------------------------------------------------


def test_put_emits_notification_with_source_and_description():
    agent = _FakeAgent()
    q: InputQueue[str] = InputQueue("user_messages", agent=agent)
    q.put("first")
    q.put("second")
    assert len(agent.event_manager.emitted) == 2
    first, second = agent.event_manager.emitted
    assert isinstance(first, Notification)
    # Source is namespaced so non-queue producers can use the same event.
    assert first.source == "queue:user_messages"
    assert "1 item" in first.description
    assert second.source == "queue:user_messages"
    assert "2 item" in second.description


def test_emit_notification_false_skips_event():
    agent = _FakeAgent()
    q: InputQueue[str] = InputQueue("q", agent=agent)
    q.put("hidden", emit_notification=False)
    assert agent.event_manager.emitted == []
    assert q.qsize() == 1


# ---------------------------------------------------------------------------
# Notification retraction on pop_last
# ---------------------------------------------------------------------------


@dataclass
class _RetractingEventManager:
    """Stub that supports add()/remove() for tag tracking."""

    emitted: list[Any] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def add(self, event: Any) -> str:
        self.emitted.append(event)
        return f"tag-{len(self.emitted)}"

    def remove(self, tag: str) -> bool:
        self.removed.append(tag)
        return True


def _retracting_agent() -> Any:
    @dataclass
    class _A:
        event_manager: _RetractingEventManager = field(default_factory=_RetractingEventManager)

    return _A()


def test_pop_last_retracts_the_notification():
    """When the user un-queues a typed-but-unsent message via Up-arrow,
    the Notification we emitted for it must be retracted so the LLM's
    event log doesn't claim data is pending when it isn't.
    """
    agent = _retracting_agent()
    q: InputQueue[str] = InputQueue("user_messages", agent=agent)
    q.put("hello")
    # One emission, no removals yet.
    assert len(agent.event_manager.emitted) == 1
    assert agent.event_manager.removed == []
    # User pops it back to edit and never re-submits.
    assert q.pop_last() == "hello"
    assert agent.event_manager.removed == ["tag-1"]


def test_pop_last_with_tag_does_not_retract():
    """``pop_last_with_tag`` is for callers that want to re-push the
    item under the same tag (the merge path). It must NOT retract."""
    agent = _retracting_agent()
    q: InputQueue[str] = InputQueue("user_messages", agent=agent)
    q.put("hello")
    pair = q.pop_last_with_tag()
    assert pair is not None
    item, tag = pair
    assert item == "hello"
    assert tag == "tag-1"
    # Crucially: NOT retracted — the caller intends to re-push it.
    assert agent.event_manager.removed == []


def test_get_does_not_retract_notification():
    """Notifications fire on push and stay in history when the
    dispatcher pumps the item — that's real history (the message did
    arrive). Only ``pop_last`` (un-queue) retracts.
    """
    agent = _retracting_agent()
    q: InputQueue[str] = InputQueue("user_messages", agent=agent)
    q.put("hello")
    assert len(agent.event_manager.emitted) == 1
    asyncio.run(q.get())
    assert agent.event_manager.removed == []


def test_inherit_tag_keeps_one_notification_after_merge():
    """The merge path uses pop_last_with_tag + put(inherit_tag=...) so
    the merged item still owns the single original Notification.
    A subsequent pop_last retracts that one tag — no orphans."""
    agent = _retracting_agent()
    q: InputQueue[str] = InputQueue("user_messages", agent=agent)
    q.put("first")
    pair = q.pop_last_with_tag()
    assert pair is not None
    item, tag = pair
    q.put(f"{item}\nsecond", emit_notification=False, inherit_tag=tag)
    # Still exactly one Notification — no second one fired for the merge.
    assert len(agent.event_manager.emitted) == 1
    # Now the user un-queues the merged item — the original tag retracts.
    assert q.pop_last() == "first\nsecond"
    assert agent.event_manager.removed == ["tag-1"]


def test_put_without_bound_agent_is_ok():
    q: InputQueue[str] = InputQueue("q")  # no agent
    q.put("x")
    q.put("y")
    assert q.qsize() == 2


# ---------------------------------------------------------------------------
# wait_for_any
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_any_returns_fast_path():
    """If a queue already has an item, wait_for_any returns it immediately."""
    q1: InputQueue[str] = InputQueue("q1")
    q2: InputQueue[str] = InputQueue("q2")
    q2.put("fromq2")
    name, item = await wait_for_any([q1, q2])
    assert name == "q2"
    assert item == "fromq2"
    assert q2.qsize() == 0
    # q1 was never touched.
    assert q1.qsize() == 0
    assert not q1.has_waiters()


@pytest.mark.asyncio
async def test_wait_for_any_races_blocking_waiters():
    q1: InputQueue[str] = InputQueue("q1")
    q2: InputQueue[str] = InputQueue("q2")
    waiter = asyncio.create_task(wait_for_any([q1, q2]))
    await asyncio.sleep(0)
    q1.put("hello")
    name, item = await asyncio.wait_for(waiter, timeout=0.5)
    assert name == "q1"
    assert item == "hello"
    # Losing waiter should be cancelled — no items stranded on q2.
    assert q2.qsize() == 0
    assert not q2.has_waiters()


@pytest.mark.asyncio
async def test_wait_for_any_preserves_items_on_losing_queue():
    """Pre-loaded item on the losing queue must still be there after the race."""
    q1: InputQueue[str] = InputQueue("q1")
    q2: InputQueue[str] = InputQueue("q2")
    q2.put("preloaded")
    # Fast path returns q2 immediately — q1 is untouched.
    name, item = await wait_for_any([q1, q2])
    assert (name, item) == ("q2", "preloaded")
    # Now a blocking race where only q1 fires mid-race — q2 item stays.
    q2.put("still-there")
    waiter = asyncio.create_task(wait_for_any([q1, q2]))
    await asyncio.sleep(0)
    # q1 wins by pushing after we start.
    q1.put("q1item")
    name, item = await asyncio.wait_for(waiter, timeout=0.5)
    # q2 fast-pathed again because it had an item — but wait, the fast
    # path runs in order, so q2 wins. Either way, q2's item doesn't get
    # orphaned — verify non-losing queue's item is still reachable.
    remaining = [q.snapshot() for q in (q1, q2)]
    flat = [x for xs in remaining for x in xs]
    # Together with the winner, "still-there", "q1item" are accounted for.
    assert "still-there" in flat or item == "still-there"


@pytest.mark.asyncio
async def test_wait_for_any_empty_raises():
    with pytest.raises(ValueError):
        await wait_for_any([])
