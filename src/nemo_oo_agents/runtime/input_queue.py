# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Async input queue for producer → agent handoffs.

An ``InputQueue`` is a named, deque-backed async queue that lives on an
agent as an instance attribute (e.g. ``self.user_messages``). Producers
push with ``put()``; the agent drains with ``await queue.get()``.

Queue state is surfaced to the LLM through a dynamic context block
(``queue_status``) that re-evaluates each turn — the agent reads the
current pending counts straight from the context, so the queue itself
doesn't need to emit any events of its own.

Compared to ``asyncio.Queue``:
- Supports ``pop_last()`` so the UI can undo "oops, I queued the
  wrong thing" without consuming awaiters.
- No maxsize / backpressure — input queues are producer-bounded by
  humans and fast job outputs, not LLM rate.
- Intentionally no ``peek()``: in the per-turn ``respond()`` model
  the dispatcher already delivers the next item via a follow-up
  turn, so the LLM has no reason to branch on queue contents.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _preview_item(item: Any, max_chars: int) -> str:
    """Render one queue item as a single-line preview clipped to *max_chars*.

    Strings render as-is; non-strings go through :func:`agentdoc.pformat`
    so a deeply nested dict/list comes out bounded by default. Newlines
    (and tabs) flatten to ``↵`` / spaces so a pasted multi-line message
    or pformat'd object stays on one preview line, and the whole thing
    is clipped with an ellipsis when it still overflows.
    """
    from nemo_oo_agents.agentdoc import pformat

    if isinstance(item, str):
        text = item
    else:
        text = pformat(item, max_length=max_chars, max_string=max_chars, max_depth=2)
    text = text.replace("\r\n", "\n").replace("\n", "↵").replace("\t", " ")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


class InputQueue[T]:
    """A named async queue owned by an agent.

    Attributes
    ----------
    name
        Queue name. Shows up in the dispatcher's ``(queue_name, item)``
        notification tuple and in the ``queue_status`` context block.
    """

    def __init__(
        self,
        name: str,
        agent: Any = None,
        *,
        on_get: Callable[[T], None] | None = None,
    ) -> None:
        self.name = name
        self._agent = agent
        self._items: deque[T] = deque()
        # Waiters are futures pending on get(). FIFO.
        self._waiters: deque[asyncio.Future[T]] = deque()
        # Hook fired once per item at the exact "queued → accepted"
        # transition, regardless of whether the caller is the outer
        # dispatcher or agent code awaiting ``get()`` mid-turn. The
        # TUI uses it to echo the user-bar and log a TUIUserInput
        # event even when the agent drains the queue itself — without
        # the hook, mid-turn ``get()`` makes the message silently
        # vanish from the UI.
        self._on_get: Callable[[T], None] | None = on_get

    # ---- producer side -----------------------------------------------

    def put(self, item: T) -> None:
        """Enqueue an item and wake one pending ``get()``.

        Fast path: if a ``get()`` is already awaiting, hand the item
        directly to the waiter (skipping the deque). Slow path: append
        to the deque so any later consumer sees it.
        """
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(item)
                return
        self._items.append(item)

    # ---- consumer side -----------------------------------------------

    async def get(self) -> T:
        """Block until an item arrives, then return it.

        Safe to cancel: a cancelled ``get()`` simply removes its waiter
        from the queue without consuming any item.

        Fires ``on_get(item)`` on the caller's event loop thread
        exactly once per returned item, whether it came straight off
        the backlog or was handed directly through a pending waiter.
        """
        if self._items:
            item = self._items.popleft()
        else:
            loop = asyncio.get_event_loop()
            waiter: asyncio.Future[T] = loop.create_future()
            self._waiters.append(waiter)
            try:
                item = await waiter
            except asyncio.CancelledError:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
                # If we were cancelled but a producer already set our result,
                # the item would be lost — push it back onto the queue. We
                # do NOT fire on_get here: the item is back in the backlog
                # and will fire when some other consumer actually dequeues it.
                if waiter.done() and not waiter.cancelled():
                    try:
                        self._items.appendleft(waiter.result())
                    except BaseException:
                        pass
                raise

        self._fire_on_get(item)
        return item

    def _fire_on_get(self, item: T) -> None:
        """Fire ``on_get`` for *item*, swallowing and logging any error.

        A buggy hook must not prevent the caller from receiving the
        item it just dequeued — dropping the item would be worse than
        a missing UI echo.
        """
        if self._on_get is None:
            return
        try:
            self._on_get(item)
        except Exception:
            logger.exception("InputQueue(%s).on_get raised", self.name)

    def set_on_get(self, callback: Callable[[T], None] | None) -> None:
        """Late-bind the ``on_get`` hook.

        Useful when the queue is constructed before the consumer that
        wants to observe dequeues exists (e.g. the TUI ``Session``
        wires its user-bar renderer here *after* the agent has already
        created its ``_user_messages_in`` queue).
        """
        self._on_get = callback

    def qsize(self) -> int:
        """Number of pending items."""
        return len(self._items)

    def is_empty(self) -> bool:
        return not self._items

    def snapshot(self) -> list[T]:
        """Return a copy of pending items (head to tail). Non-consuming."""
        return list(self._items)

    def pop_last(self) -> T | None:
        """Remove and return the most recently put item (tail).

        Used by the TUI for "edit what I just queued" UX (Up-arrow):
        the user pulls the queued message back into the input buffer
        to edit it. Returns None if the queue is empty.
        """
        if not self._items:
            return None
        return self._items.pop()

    def clear(self) -> None:
        self._items.clear()

    def has_waiters(self) -> bool:
        """True if a consumer is currently blocked on ``get()``.

        UI uses this to decide whether the agent is "idle" (blocked
        waiting for input) vs "thinking" (working the queue).
        """
        return any(not w.done() for w in self._waiters)

    # ---- introspection -----------------------------------------------

    def status(self, *, max_items: int = 3, max_chars: int = 80) -> str:
        """Format a pending-count summary + short preview of waiting items.

        Returns an empty string when the queue is drained so callers can
        conditionally include it. Used by the TUI's ``queue_status``
        dynamic context block and available to the LLM directly via
        ``OutputQueue.status()`` for mid-turn checks.

        Example::

            user_messages: 2 pending
              1. Can you fix this error?↵↵# one-time setup (if necess…
              2. OK, keep going.
        """
        if not self._items:
            return ""
        lines = [f"{self.name}: {len(self._items)} pending"]
        snapshot = list(self._items)
        for i, item in enumerate(snapshot[:max_items], start=1):
            lines.append(f"  {i}. {_preview_item(item, max_chars)}")
        overflow = len(snapshot) - max_items
        if overflow > 0:
            lines.append(f"  … {overflow} more")
        return "\n".join(lines)

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

    def status(self, *, max_items: int = 3, max_chars: int = 80) -> str:
        """Delegate to the underlying ``InputQueue.status()``.

        Exposed on the read facade so the LLM can peek at pending items
        mid-turn (``self.user_messages.status()``) without reaching into
        the hidden producer-side queue.
        """
        return self._source.status(max_items=max_items, max_chars=max_chars)

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
        for t in pending:
            try:
                await t
            except (asyncio.CancelledError, BaseException):
                pass
        winner = next(iter(done))
        return tasks[winner].name, winner.result()
    except BaseException:
        # On outer cancellation, cancel everything in flight.
        for t in tasks:
            t.cancel()
        raise
