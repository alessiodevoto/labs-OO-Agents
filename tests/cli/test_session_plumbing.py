# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for plumbing inside ``Session`` and ``TUIConsole``.

These cover seams the post-landing test-coverage review flagged:

- G4: ``_EmitStream`` must coalesce one ``Console.print`` into one
  ``emit_block`` call (not one per chunk).
- G5: ``TUIConsole.replace_console`` swaps the underlying Rich console.
- G6: ``Session._cancel_background_tasks`` cancels and awaits every
  tracked task and leaves the set empty.

The style is direct construction with no prompt_toolkit / no harness —
these components don't need an ``Application`` to be testable.
"""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.table import Table

from nemo_oo_agents_cli.tui.console import TUIConsole
from nemo_oo_agents_cli.tui.session import _EmitStream


def test_emit_stream_coalesces_one_print_into_one_emit_call() -> None:
    """Rich writes many small chunks per ``Console.print``. ``_EmitStream``
    must buffer until the trailing ``flush()`` and call ``emit`` exactly
    once — otherwise each styled span pays a ``run_in_terminal`` hop."""
    calls: list[str] = []
    stream = _EmitStream(calls.append)

    table = Table(title="test")
    table.add_column("a")
    table.add_column("b")
    table.add_row("1", "2")
    table.add_row("3", "4")

    # Emulate what the redirected Rich console does: file=stream, print, flush.
    Console(file=stream, force_terminal=True, color_system="256", width=80).print(table)

    assert len(calls) == 1, f"expected 1 emit call, got {len(calls)}"
    # And the coalesced block has the full table content.
    assert "test" in calls[0]
    assert "a" in calls[0] and "b" in calls[0]


def test_emit_stream_empty_flush_is_noop() -> None:
    """Flushing an empty buffer doesn't emit a stray empty block."""
    calls: list[str] = []
    stream = _EmitStream(calls.append)
    stream.flush()
    assert calls == []


def test_emit_stream_multiple_prints_produce_multiple_emits() -> None:
    """Each ``Console.print`` flushes at the end → one emit per print."""
    calls: list[str] = []
    stream = _EmitStream(calls.append)
    c = Console(file=stream, force_terminal=True, color_system="256", width=80)
    c.print("first")
    c.print("second")
    assert len(calls) == 2


def test_tui_console_replace_console_swaps_underlying_console() -> None:
    """``replace_console`` is the public seam Session uses to redirect
    frontend output. A replaced console is what ``print_agent`` /
    ``print_table`` / etc. end up writing to."""
    tui = TUIConsole()
    original = tui.console

    # A StringIO-backed console so we can inspect what was written.
    import io as _io

    captured_file = _io.StringIO()
    replacement = Console(file=captured_file, force_terminal=True, color_system="256", width=80)
    tui.replace_console(replacement)

    assert tui.console is replacement
    assert tui.console is not original

    # A call that routes through ``tui.console`` should land in our file.
    tui.console.print("hello-from-replacement")
    assert "hello-from-replacement" in captured_file.getvalue()


async def test_session_cancel_background_tasks_cancels_and_clears() -> None:
    """``_cancel_background_tasks`` cancels every tracked task, awaits
    their cancellation, and empties the set."""
    from nemo_oo_agents_cli.tui.session import Session

    # Minimal Session stub — we only touch ``_background_tasks`` and
    # ``_cancel_background_tasks``. Avoids the real Session's config/agent
    # construction dance.
    session = Session.__new__(Session)
    session._background_tasks = set()

    async def _long_running() -> None:
        await asyncio.sleep(60)

    t1 = asyncio.create_task(_long_running())
    t2 = asyncio.create_task(_long_running())
    session._background_tasks.update({t1, t2})

    await session._cancel_background_tasks()

    assert session._background_tasks == set()
    assert t1.cancelled() and t2.cancelled()


async def test_session_cancel_background_tasks_is_safe_when_empty() -> None:
    """No tracked tasks → the helper returns immediately; no errors."""
    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session._background_tasks = set()

    await session._cancel_background_tasks()
    assert session._background_tasks == set()


async def test_session_cancel_background_tasks_skips_done_tasks() -> None:
    """Tasks that already finished aren't cancelled (a no-op), but the
    set is still cleared."""
    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session._background_tasks = set()

    async def _noop() -> None:
        return

    t = asyncio.create_task(_noop())
    await t  # task completes before cancel_background_tasks runs
    session._background_tasks.add(t)

    await session._cancel_background_tasks()
    assert session._background_tasks == set()
    assert not t.cancelled()  # done tasks aren't flipped to cancelled
