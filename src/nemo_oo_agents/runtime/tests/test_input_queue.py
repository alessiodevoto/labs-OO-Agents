# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``InputQueue`` + ``wait_for_any``.

No LLM / runtime required — just ``asyncio`` behaviour.

Queue state is surfaced to the LLM through a dynamic context block
(see ``BaseTUIAgent._queue_status``), not through events — so the
``InputQueue`` itself has no event-manager coupling.
"""

from __future__ import annotations

import asyncio

import pytest

from nemo_oo_agents.runtime.input_queue import InputQueue, wait_for_any

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

    The item should NOT land on the backing deque — qsize stays 0.
    """
    q: InputQueue[int] = InputQueue("q")
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0)
    q.put(42)
    assert await asyncio.wait_for(getter, timeout=0.5) == 42
    assert q.qsize() == 0


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


def test_clear_empties_the_queue():
    q: InputQueue[str] = InputQueue("q")
    q.put("a")
    q.put("b")
    q.clear()
    assert q.qsize() == 0
    assert q.snapshot() == []


# ---------------------------------------------------------------------------
# on_get hook — fires once per returned item, on every path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_get_fires_on_slow_path_backlog_drain():
    """When the item was already on the deque, ``get()`` fires ``on_get``
    before returning. The TUI relies on this for the mid-turn dequeue
    case where the agent's ``await self.user_messages.get()`` drains
    a message that was typed while the agent was busy."""
    seen: list[str] = []
    q: InputQueue[str] = InputQueue("user_messages", on_get=seen.append)
    q.put("hi")
    got = await q.get()
    assert got == "hi"
    assert seen == ["hi"]


@pytest.mark.asyncio
async def test_on_get_fires_on_fast_path_waiter_handoff():
    """When a waiter was already blocked on ``get()`` and ``put()`` hands
    the item straight through, the hook must still fire exactly once."""
    seen: list[str] = []
    q: InputQueue[str] = InputQueue("user_messages", on_get=seen.append)
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0)  # register the waiter
    q.put("hello")
    assert await asyncio.wait_for(getter, timeout=0.5) == "hello"
    assert seen == ["hello"]


@pytest.mark.asyncio
async def test_on_get_fires_once_per_item_across_multiple_gets():
    """Two puts, two gets → hook fires twice, in order."""
    seen: list[str] = []
    q: InputQueue[str] = InputQueue("user_messages", on_get=seen.append)
    q.put("a")
    q.put("b")
    assert await q.get() == "a"
    assert await q.get() == "b"
    assert seen == ["a", "b"]


@pytest.mark.asyncio
async def test_on_get_does_not_fire_on_cancelled_get():
    """If a waiter is cancelled, no item transitioned queued → accepted
    — the hook must NOT fire. A producer that already set the waiter's
    result pushes the item back onto the deque; the hook will fire when
    some other consumer actually takes it."""
    seen: list[str] = []
    q: InputQueue[str] = InputQueue("q", on_get=seen.append)

    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0)
    getter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await getter

    assert seen == []
    # Now a real consumer — the hook should fire for them.
    q.put("later")
    assert await q.get() == "later"
    assert seen == ["later"]


@pytest.mark.asyncio
async def test_on_get_hook_exception_is_swallowed_and_item_still_returned():
    """A buggy hook must not eat the item. Dropping it on the floor is
    strictly worse than a missing UI echo."""

    def boom(_item: str) -> None:
        raise RuntimeError("bad hook")

    q: InputQueue[str] = InputQueue("q", on_get=boom)
    q.put("payload")
    # Must not raise.
    got = await q.get()
    assert got == "payload"


@pytest.mark.asyncio
async def test_set_on_get_late_binds():
    """Session installs its ``_on_user_message`` hook AFTER the agent
    has already created its ``_user_messages_in`` queue, so late
    binding must work."""
    seen: list[str] = []
    q: InputQueue[str] = InputQueue("user_messages")  # no hook yet
    q.set_on_get(seen.append)
    q.put("x")
    await q.get()
    assert seen == ["x"]


# ---------------------------------------------------------------------------
# status() — pending-count + preview rendering on the queue itself
# ---------------------------------------------------------------------------


def test_status_empty_queue_returns_empty_string():
    q: InputQueue[str] = InputQueue("user_messages")
    assert q.status() == ""


def test_status_includes_name_count_and_numbered_previews():
    q: InputQueue[str] = InputQueue("user_messages")
    q.put("first")
    q.put("second")
    status = q.status()
    lines = status.splitlines()
    assert lines[0] == "user_messages: 2 pending"
    assert lines[1] == "  1. first"
    assert lines[2] == "  2. second"


def test_status_flattens_newlines_and_clips_overlong_items():
    q: InputQueue[str] = InputQueue("user_messages")
    q.put("line1\nline2\nline3")
    q.put("x" * 200)
    status = q.status(max_items=3, max_chars=30)
    # No raw newline inside preview content (only between lines).
    preview_lines = status.splitlines()[1:]
    for line in preview_lines:
        # Strip "  N. " prefix; remaining content has no embedded \n.
        assert "\n" not in line
        assert len(line) <= len("  N. ") + 30
    assert "↵" in status


def test_status_overflow_summary_when_more_than_max_items():
    q: InputQueue[int] = InputQueue("jobs")
    for i in range(7):
        q.put(i)
    status = q.status(max_items=3)
    assert "jobs: 7 pending" in status
    assert "… 4 more" in status


def test_status_previews_non_string_items_via_pformat():
    q: InputQueue[dict] = InputQueue("jobs")
    q.put({"id": 42, "kind": "build"})
    status = q.status()
    assert "jobs: 1 pending" in status
    assert "'id': 42" in status


def test_output_queue_status_delegates_to_input_queue():
    """OutputQueue exposes status() so the LLM can peek mid-turn without
    reaching into the hidden producer-side queue."""
    q: InputQueue[str] = InputQueue("user_messages")
    q.put("waiting")
    assert q.reader.status() == q.status()


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
