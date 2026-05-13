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

from nemo_oo_agents_cli.tui.console import TUIConsole
from nemo_oo_agents_cli.tui.session import _EmitStream
from rich.console import Console
from rich.table import Table


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


def test_print_exit_message_includes_name_and_short_hash(capsys) -> None:
    """When the session has a name and id, the exit message tags both
    in the form ``name [first8charsofhash]`` — same shape as the
    session-label rendered above the input bar."""
    from unittest.mock import Mock

    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session._session_manager = Mock()
    session._session_manager.session_id = "abc1234567890def"
    session._session_manager.name = "my-debug-run"

    session._print_exit_message()
    err = capsys.readouterr().err
    assert "Goodbye! Stay vibing." in err
    assert "my-debug-run [abc12345]" in err


def test_print_exit_message_short_hash_only_when_name_missing(capsys) -> None:
    """Unnamed sessions still get the bracketed short-hash tag."""
    from unittest.mock import Mock

    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session._session_manager = Mock()
    session._session_manager.session_id = "deadbeefcafebabe"
    session._session_manager.name = None

    session._print_exit_message()
    err = capsys.readouterr().err
    assert "Goodbye! Stay vibing. — [deadbeef]" in err


def test_print_exit_message_no_session_manager(capsys) -> None:
    """Sessions without a session_manager still get the goodbye line,
    just without a tag."""
    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session._session_manager = None

    session._print_exit_message()
    err = capsys.readouterr().err
    assert "Goodbye! Stay vibing." in err
    # Strip ANSI before checking for absence of a bracketed session tag.
    import re

    plain = re.sub(r"\x1b\[[0-9;]*m", "", err)
    assert "[" not in plain  # no bracketed tag


async def test_session_on_user_message_fires_when_dispatcher_dequeues() -> None:
    """Regression guard for the original bug: the user-bar echo wiring
    used to assign to ``self._app.on_user_message``, an attribute that
    nothing reads. The fix installs ``Session._on_user_message`` on
    the channel's ``on_get`` hook, so the echo fires when the
    dispatcher dequeues the message.
    """
    from unittest.mock import Mock, patch

    from nemo_oo_agents_cli.tui.session import Session

    from nemo_oo_agents.runtime.channels import Channel

    session = Session.__new__(Session)
    session._renderer = Mock()
    session._app = Mock()
    session._app.color_depth = 8
    session._session_manager = Mock()
    session._session_manager.user_named = True  # skip auto-name path
    session._first_message = None
    # _colors is a read-only property that reads the global theme; no setup needed.

    queue: Channel[str] = Channel("user_messages", "queue")
    # The exact wiring Session.run() performs.
    queue.set_on_get(session._on_user_message)

    queue.put("hi from dispatcher")
    with patch("nemo_oo_agents_cli.tui.session._build_user_bar", return_value="BAR"):
        item = await queue.get()
    assert item == "hi from dispatcher"

    session._session_manager.record_user.assert_called_once_with("hi from dispatcher")
    session._renderer.reset_turn.assert_called_once()
    session._app.emit_block.assert_called_once_with("BAR")


async def test_session_on_user_message_fires_for_mid_turn_dequeue() -> None:
    """Symmetry: ``on_get`` lives on the queue (not the dispatcher loop)
    precisely so the echo fires when the agent drains mid-turn via
    ``await self.user_messages.get()`` — not just when the dispatcher
    dequeues. If a future refactor moves the call into the dispatcher
    loop, the mid-turn drain path silently drops user-bar / TUIUserInput.
    """
    from unittest.mock import Mock, patch

    from nemo_oo_agents_cli.tui.session import Session

    from nemo_oo_agents.runtime.channels import Channel

    session = Session.__new__(Session)
    session._renderer = Mock()
    session._app = Mock()
    session._app.color_depth = 8
    session._session_manager = Mock()
    session._session_manager.user_named = True
    session._first_message = None
    # _colors is a read-only property that reads the global theme; no setup needed.

    inq: Channel[str] = Channel("user_messages", "queue")
    inq.set_on_get(session._on_user_message)
    # Mid-turn drain goes through the read facade, the same surface
    # the LLM uses.
    reader = inq.reader

    inq.put("clarification")
    with patch("nemo_oo_agents_cli.tui.session._build_user_bar", return_value="BAR"):
        item = await reader.get()
    assert item == "clarification"

    # Must fire exactly once — symmetric with the dispatcher path.
    session._session_manager.record_user.assert_called_once_with("clarification")
    session._renderer.reset_turn.assert_called_once()
    session._app.emit_block.assert_called_once_with("BAR")


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


async def test_on_command_clear_cancels_agent_task() -> None:
    """``/clear`` while the agent is mid-turn must cancel ``_agent_task``
    so the old turn doesn't keep running in the stale session."""
    from unittest.mock import AsyncMock, MagicMock

    from nemo_oo_agents_cli.tui.commands import CommandResult
    from nemo_oo_agents_cli.tui.output import ClearScreen
    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session._first_message = "hello"
    session._background_tasks = set()
    session._emit_console = None

    agent = MagicMock()
    agent._storage = MagicMock()
    agent.event_manager = MagicMock()
    agent.event_manager.set_backend = MagicMock()
    agent.queue_manager = MagicMock()
    agent.queue_manager.shutdown = AsyncMock()
    agent.queue_manager._channels = {}
    agent.queue_manager.names = MagicMock(return_value=[])
    session.agent = agent

    registry = MagicMock()
    registry.commands = MagicMock(return_value=[])
    session.registry = registry
    session._session_manager = MagicMock()
    session._session_manager.close = MagicMock()

    # Simulate a running agent task
    async def _fake_agent_work():
        await asyncio.sleep(999)

    fake_task = asyncio.ensure_future(_fake_agent_work())

    app = MagicMock()
    app._agent_task = fake_task
    session._app = app
    session._emit_text = MagicMock()

    # Make _handler.handle return a result with new_session_manager
    new_sm = MagicMock()
    new_sm.session_id = "new-session-id"
    new_sm._storage = MagicMock()

    fake_result = CommandResult(success=True, outputs=[ClearScreen()])
    fake_result.new_session_manager = new_sm

    handler = MagicMock()
    handler.handle = AsyncMock(return_value=fake_result)
    session._handler = handler

    # Mock _swap_session_manager so it doesn't try real session operations
    session._swap_session_manager = AsyncMock()

    await session._on_command("/clear")

    assert fake_task.cancelled(), (
        f"_agent_task not cancelled; done={fake_task.done()}, "
        f"cancelled={fake_task.cancelled()}"
    )
    assert session._first_message is None, "_first_message not reset after /clear"
    session._swap_session_manager.assert_awaited_once_with(new_sm)


async def test_on_command_clear_without_running_task() -> None:
    """``/clear`` when no agent task is running must not crash."""
    from unittest.mock import AsyncMock, MagicMock

    from nemo_oo_agents_cli.tui.commands import CommandResult
    from nemo_oo_agents_cli.tui.output import ClearScreen
    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session._first_message = "hello"
    session._background_tasks = set()

    agent = MagicMock()
    agent._storage = MagicMock()
    agent.event_manager = MagicMock()
    agent.event_manager.set_backend = MagicMock()
    agent.queue_manager = MagicMock()
    agent.queue_manager.shutdown = AsyncMock()
    agent.queue_manager._channels = {}
    agent.queue_manager.names = MagicMock(return_value=[])
    session.agent = agent

    registry = MagicMock()
    registry.commands = MagicMock(return_value=[])
    session.registry = registry
    session._session_manager = MagicMock()
    session._session_manager.close = MagicMock()

    app = MagicMock()
    app._agent_task = None  # no running task
    session._app = app
    session._emit_text = MagicMock()

    new_sm = MagicMock()
    new_sm.session_id = "new-session-id"
    new_sm._storage = MagicMock()

    fake_result = CommandResult(success=True, outputs=[ClearScreen()])
    fake_result.new_session_manager = new_sm

    handler = MagicMock()
    handler.handle = AsyncMock(return_value=fake_result)
    session._handler = handler

    # Mock _swap_session_manager so it doesn't try real session operations
    session._swap_session_manager = AsyncMock()

    # Must not raise
    await session._on_command("/clear")
    assert session._first_message is None
