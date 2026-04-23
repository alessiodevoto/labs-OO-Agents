# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Async input queue with Notification emission.

An ``InputQueue`` is a named, deque-backed async queue that lives on an
agent as an instance attribute (e.g. ``self.user_messages``). Pushing
onto the queue fires a ``Notification`` event so the LLM knows data is
available; the agent drains with ``await queue.get()``.

Compared to ``asyncio.Queue``:
- Supports ``pop_last()`` so the UI can undo "oops, I queued the
  wrong thing" without consuming awaiters.
- Auto-emits a ``Notification`` event on each ``put()`` so the LLM sees
  queue activity in its context.
- No maxsize / backpressure — input queues are producer-bounded by
  humans and fast job outputs, not LLM rate.
- Intentionally no ``peek()``: in the per-turn ``respond()`` model
  the dispatcher already delivers the next item via a follow-up
  turn, so the LLM has no reason to branch on queue contents.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from nemo_oo_agents.events import Notification


class InputQueue[T]:
    """A named async queue that emits a ``Notification`` event on every push.

    Attributes
    ----------
    name
        Queue name. Shows up in ``Notification.queue_name`` and is how
        ``Agent.get_next_input(name)`` looks the queue up.
    """

    def __init__(self, name: str, agent: Any = None) -> None:
        self.name = name
        self._agent = agent
        # Each queued slot is (item, notification_tag_or_None). Tracking
        # the tag lets ``pop_last`` retract the Notification when a
        # producer (e.g. UI Up-arrow) un-queues an item — keeping the
        # event log truthful instead of leaving a "1 item pending"
        # event that the LLM will see for nothing.
        self._items: deque[tuple[T, str | None]] = deque()
        # Waiters are futures pending on get(). FIFO.
        self._waiters: deque[asyncio.Future[T]] = deque()

    # ---- producer side -----------------------------------------------

    def put(
        self,
        item: T,
        *,
        emit_notification: bool = True,
        inherit_tag: str | None = None,
    ) -> None:
        """Enqueue an item and wake one pending ``get()``.

        On the slow path (no awaiting consumer), emits a ``Notification``
        and stores its tag alongside the item so ``pop_last`` can
        retract it later. Pass ``emit_notification=False`` to skip
        emission. Pass ``inherit_tag=<tag>`` to attach a previously-
        emitted tag to this item (used by the merge path in
        ``submit_message`` so a merged item keeps the original
        Notification rather than firing a duplicate).

        Fast path: if a ``get()`` is already awaiting, hand the item
        directly to the waiter (skipping the deque). NO notification
        fires in this case — the consumer's awaited return value IS
        the notification. Emitting one would just clutter the event
        log with "data arrived" right next to the item itself.
        """
        # Hand directly to a waiting get() if one exists. This keeps
        # the deque at 0 and avoids a wake-then-poll round-trip; the
        # consumer's await returns immediately with the item.
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(item)
                return

        # Slow path: queue the item and (optionally) emit a Notification
        # so any later consumer learns the queue has data.
        tag: str | None = inherit_tag
        if emit_notification and inherit_tag is None:
            tag = self._emit_notification(pending=len(self._items) + 1)
        self._items.append((item, tag))

    def _emit_notification(self, *, pending: int) -> str | None:
        """Emit a Notification on the agent's event_manager. Returns the
        assigned tag (so it can be paired with the queue slot for later
        retraction), or None if no event_manager is bound.
        """
        if self._agent is None:
            return None
        mgr = getattr(self._agent, "event_manager", None)
        if mgr is None:
            return None
        return mgr.add(
            Notification(
                source=f"queue:{self.name}",
                description=f"{pending} item(s) pending on queue {self.name!r}",
            )
        )

    # ---- consumer side -----------------------------------------------

    async def get(self) -> T:
        """Block until an item arrives, then return it.

        Safe to cancel: a cancelled ``get()`` simply removes its waiter
        from the queue without consuming any item.

        Note: the Notification associated with the popped item is
        *not* retracted here — when the dispatcher hands an item to
        ``respond()`` the Notification is real history. Only
        ``pop_last`` (un-queue without delivering) retracts.
        """
        if self._items:
            item, _tag = self._items.popleft()
            return item
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[T] = loop.create_future()
        self._waiters.append(waiter)
        try:
            return await waiter
        except asyncio.CancelledError:
            # Clean up — remove ourselves from the waiter list if still there.
            try:
                self._waiters.remove(waiter)
            except ValueError:
                pass
            # If we were cancelled but a producer already set our result,
            # the item would be lost — push it back onto the queue with
            # no notification tag (that one already fired and stays).
            if waiter.done() and not waiter.cancelled():
                try:
                    self._items.appendleft((waiter.result(), None))
                except BaseException:
                    pass
            raise

    def qsize(self) -> int:
        """Number of pending items."""
        return len(self._items)

    def is_empty(self) -> bool:
        return not self._items

    def snapshot(self) -> list[T]:
        """Return a copy of pending items (head to tail). Non-consuming."""
        return [item for item, _tag in self._items]

    def pop_last(self) -> T | None:
        """Remove and return the most recently put item (tail).

        Used by the TUI for "edit what I just queued" UX (Up-arrow):
        the user pulls the queued message back into the input buffer
        to edit it. The item never reached the agent, so we *also*
        retract the Notification we emitted for it — otherwise the
        LLM's event log claims data is pending when there isn't.

        Returns None if the queue is empty.
        """
        if not self._items:
            return None
        item, tag = self._items.pop()
        if tag is not None:
            self._retract_notification(tag)
        return item

    def pop_last_with_tag(self) -> tuple[T, str | None] | None:
        """``pop_last`` variant that also returns the notification tag
        WITHOUT retracting it. Used by callers that want to re-push
        the same item under the same tag (the merge path in
        ``submit_message``).
        """
        if not self._items:
            return None
        return self._items.pop()

    def _retract_notification(self, tag: str) -> None:
        if self._agent is None:
            return
        mgr = getattr(self._agent, "event_manager", None)
        if mgr is None:
            return
        try:
            mgr.remove(tag)
        except Exception:
            pass

    def clear(self) -> None:
        self._items.clear()

    def has_waiters(self) -> bool:
        """True if a consumer is currently blocked on ``get()``.

        UI uses this to decide whether the agent is "idle" (blocked
        waiting for input) vs "thinking" (working the queue).
        """
        return any(not w.done() for w in self._waiters)

    # ---- lifecycle ---------------------------------------------------

    def bind_agent(self, agent: Any) -> None:
        """Late-bind the agent (for queues constructed before super().__init__)."""
        self._agent = agent

    def __repr__(self) -> str:
        return f"InputQueue(name={self.name!r}, pending={len(self._items)})"

    @property
    def reader(self) -> OutputQueue[T]:
        """Return (cached) the LLM-facing read facade for this queue.

        Use this when attaching a queue to an agent: keep the
        ``InputQueue`` itself on a ``hidden`` attribute (it has
        ``put``, ``snapshot``, etc. — full producer/dispatcher
        access) and expose the ``reader`` as the public attribute the
        LLM sees in ``doc(self)``.
        """
        cached = getattr(self, "_reader_cache", None)
        if cached is None:
            cached = OutputQueue(self)
            self._reader_cache = cached
        return cached


class OutputQueue[T]:
    """LLM-facing read facade for an ``InputQueue``.

    Exposes only ``get()`` and ``name`` — putting, snapshot, pop_last,
    qsize, has_waiters etc. are dispatcher-only and stay on the
    underlying ``InputQueue``.

    The LLM is free to call ``await self.<queue>.get()`` from inside
    ``execute_python`` whenever it wants the next item *now* without
    ending the turn (e.g. asking a clarifying question mid-task and
    waiting for the reply, or draining a few queued messages before
    deciding what to do). It blocks until something arrives.
    """

    def __init__(self, source: InputQueue[T]) -> None:
        self._source = source

    @property
    def name(self) -> str:
        return self._source.name

    async def get(self) -> T:
        return await self._source.get()

    def __repr__(self) -> str:
        return f"OutputQueue(name={self.name!r})"


async def wait_for_any(queues: list[InputQueue[Any]]) -> tuple[str, Any]:
    """Wait for the first queue to produce an item. Returns (name, item).

    Cancels losing waiters cleanly: their items stay on their queues,
    nothing is dropped. Raises ``ValueError`` if the list is empty.
    """
    if not queues:
        raise ValueError("wait_for_any() requires at least one queue")

    # Fast path: if any queue already has an item, return it without
    # creating tasks. Preserves FIFO ordering by queue-list position.
    for q in queues:
        if not q.is_empty():
            return q.name, await q.get()

    # Slow path: race their get() coroutines. Wrap each in a task so
    # we can cancel losers without losing items.
    tasks: dict[asyncio.Task[Any], InputQueue[Any]] = {
        asyncio.create_task(q.get(), name=f"wait_for_any[{q.name}]"): q for q in queues
    }
    try:
        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        # Cancel pending waiters; their .get() coros unwind cleanly
        # (InputQueue.get handles CancelledError by removing the waiter).
        for t in pending:
            t.cancel()
        # Await cancellations so exceptions don't leak.
        await asyncio.gather(*pending, return_exceptions=True)
        winner = next(iter(done))
        return tasks[winner].name, winner.result()
    except BaseException:
        # On outer cancellation, cancel everything in flight.
        for t in tasks:
            t.cancel()
        raise
