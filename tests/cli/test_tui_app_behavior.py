# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behaviour spec for ``TUIApplication`` (Plan-C, single long-lived app).

Each test pins a behaviour the TUI must preserve. Tests are grouped
by tier of concern:

* Tier 1 — baseline REPL (prompt, enter → agent, buffer clears, exit)
* Tier 2 — input mechanics (Shift+Enter, backspace, history, cursor)
* Tier 3 — commands (/slash dispatch, Tab completion, !bang)
* Tier 4 — type-ahead queue (queued lines, delivery order, cancel)
* Tier 5 — hard cases (Ctrl+C, errors, Rich ANSI, spinner, THE BUG)

Tests read logical state (``app.input_buffer.text``, ``app.state.items``,
``app.status_text()``) via the harness's ``capture_*`` helpers rather
than parsing terminal output — same discipline as the harness canaries.

The ``XFAIL`` mark is still defined below for any future test that
wants to pin an unimplemented behaviour; it's not applied anywhere
right now because every listed behaviour is implemented.
"""

from __future__ import annotations

import asyncio

import pytest

from .tui_app_harness import FakeAgent, TUIHarness

XFAIL = pytest.mark.xfail(strict=True, reason="not yet implemented in Plan-C TUIApplication")

pytestmark = pytest.mark.asyncio


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ Tier 1 — baseline REPL                                                ║
# ╚══════════════════════════════════════════════════════════════════════╝


async def test_baseline_prompt_visible_at_startup():
    """The app renders the prompt marker and the cursor lives on the input line."""
    async with TUIHarness() as h:
        await h.wait_for(lambda: h.app.prompt_char_visible())  # new API


async def test_baseline_enter_submits_to_agent():
    async with TUIHarness() as h:
        await h.type_keys("hello world")
        await h.press("enter")
        await h.wait_for(lambda: h.agent.messages_received == ["hello world"])


async def test_baseline_agent_message_renders_to_output():
    agent = FakeAgent()

    async def step(self: FakeAgent, msg: str):
        self.emit_message("Hi there!")

    agent.queue(step)
    async with TUIHarness(agent=agent) as h:
        await h.type_keys("ping")
        await h.press("enter")
        await h.wait_output_contains("Hi there!")


async def test_baseline_input_buffer_cleared_after_submit():
    async with TUIHarness() as h:
        await h.type_keys("something")
        await h.press("enter")
        await h.wait_input_equals("")


async def test_baseline_ctrl_d_exits():
    # Already pinned by the stub's Ctrl+D binding; keep un-xfailed so a
    # regression flips the test red instead of silently green-to-green.
    async with TUIHarness() as h:
        await h.press("c-d")
        await h.wait_for(lambda: not h.app.is_running)


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ Tier 2 — input mechanics                                              ║
# ╚══════════════════════════════════════════════════════════════════════╝


async def test_input_shift_enter_inserts_newline():
    async with TUIHarness() as h:
        await h.type_keys("line1")
        await h.press("s-enter")
        await h.type_keys("line2")
        await h.press("enter")
        await h.wait_for(lambda: h.agent.messages_received == ["line1\nline2"])


async def test_input_backspace_deletes_char():
    # Already provable in the stub; keep as a non-xfail regression to
    # guarantee it never breaks as we grow the implementation.
    async with TUIHarness() as h:
        await h.type_keys("abc")
        await h.press("backspace")
        await h.wait_input_equals("ab")


async def test_input_history_up_on_empty_buffer():
    """With no queue, Up on an empty buffer recalls the previous submission."""
    async with TUIHarness() as h:
        await h.type_keys("first")
        await h.press("enter")
        await h.wait_input_equals("")
        await h.press("up")
        await h.wait_input_equals("first")


async def test_input_cursor_home_and_end():
    async with TUIHarness() as h:
        await h.type_keys("abc")
        await h.press("home")
        await h.wait_for(lambda: h.app.input_cursor_position() == 0)
        await h.press("end")
        await h.wait_for(lambda: h.app.input_cursor_position() == 3)


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ Tier 3 — commands                                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝


async def test_commands_slash_dispatches_without_calling_agent():
    """``/help`` routes to the command registry, not ``agent.respond()``."""
    async with TUIHarness() as h:
        await h.type_keys("/help")
        await h.press("enter")
        await h.wait_for(lambda: "/help" in h.app.commands_dispatched())
        assert h.agent.messages_received == []


async def test_commands_slash_fires_on_command_callback():
    """Session wires ``on_command`` to route slash submissions into its
    CommandRegistry; this test pins the hook contract."""
    from nemo_oo_agents_cli.tui.tui_application import TUIApplication

    received: list[str] = []
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from .tui_app_harness import FakeAgent

    agent = FakeAgent()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = TUIApplication(agent=agent, on_command=received.append)
        import asyncio as _a

        run_task = _a.create_task(app.run_async())
        # wait for ready
        for _ in range(200):
            if app.is_running:
                break
            await _a.sleep(0.01)
        pipe.send_text("/help\r")
        for _ in range(200):
            if received:
                break
            await _a.sleep(0.01)
        assert received == ["/help"]
        app.exit()
        try:
            await _a.wait_for(run_task, 2.0)
        except (TimeoutError, _a.CancelledError):
            run_task.cancel()


async def test_commands_bang_fires_on_bang_callback_with_stripped_body():
    """``on_bang`` receives the body without the leading ``!``."""
    from nemo_oo_agents_cli.tui.tui_application import TUIApplication

    received: list[str] = []
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from .tui_app_harness import FakeAgent

    agent = FakeAgent()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = TUIApplication(agent=agent, on_bang=received.append)
        import asyncio as _a

        run_task = _a.create_task(app.run_async())
        for _ in range(200):
            if app.is_running:
                break
            await _a.sleep(0.01)
        pipe.send_text("!echo hi\r")
        for _ in range(200):
            if received:
                break
            await _a.sleep(0.01)
        assert received == ["echo hi"]
        app.exit()
        try:
            await _a.wait_for(run_task, 2.0)
        except (TimeoutError, _a.CancelledError):
            run_task.cancel()


async def test_commands_tab_completion_for_slash():
    async with TUIHarness() as h:
        await h.type_keys("/he")
        await h.press("tab")
        # Completion menu should list /help; input buffer either expanded
        # to "/help" or showed the menu — we accept either.
        await h.wait_for(
            lambda: h.capture_input() == "/help" or "/help" in h.app.completion_candidates()
        )


async def test_commands_bang_suspends_app_and_runs_shell():
    """``!echo hi`` uses ``run_in_terminal`` so stdout goes to the real tty."""
    async with TUIHarness() as h:
        await h.type_keys("!echo hi")
        await h.press("enter")
        # Implementation should record that it asked the terminal to
        # suspend + run a shell command. In production that's
        # prompt_toolkit's ``run_in_terminal``; we assert the hook was
        # called with the right command string.
        await h.wait_for(lambda: h.app.last_bang_command() == "echo hi")


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ Tier 4 — type-ahead queue                                             ║
# ╚══════════════════════════════════════════════════════════════════════╝


def _blocking_agent() -> FakeAgent:
    """Agent whose ``respond()`` blocks until we manually set ``block``."""
    agent = FakeAgent()
    agent.block = asyncio.Event()  # unset → respond() blocks on wait()
    return agent


async def test_queue_displays_above_prompt_while_agent_working():
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.type_keys("trigger")
        await h.press("enter")
        # Agent is now blocked. User type-aheads a message.
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("queued-msg")
        await h.press("enter")
        await h.wait_for(lambda: h.capture_queued() == ["queued-msg"])


async def test_queue_multiple_enters_merge_into_one_item():
    """Successive Enters typed while the agent is working compose one
    queued message joined with newlines — restores the old
    ``QueueState.submit`` UX so the agent isn't asked to handle each
    line of a half-finished thought as its own turn."""
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("trigger")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("one")
        await h.press("enter")
        await h.type_keys("two")
        await h.press("enter")
        # One queue item, two lines.
        await h.wait_for(lambda: h.capture_queued() == ["one\ntwo"])
        # Three lines if the user keeps going.
        await h.type_keys("three")
        await h.press("enter")
        await h.wait_for(lambda: h.capture_queued() == ["one\ntwo\nthree"])


async def test_slash_command_while_agent_working_dispatches_immediately():
    """Forever-loop model: slash commands no longer queue — they fire right away.

    Contrast with the old contract where /exit typed mid-turn waited
    for the agent to finish. Now the agent's respond() runs for the
    whole session, so there's no "next turn" to flush commands into.
    """
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("trigger")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("/exit")
        await h.press("enter")
        await h.wait_for(lambda: h.app.commands_dispatched() == ["/exit"])


async def test_queue_up_arrow_pops_last_queued_item_when_buffer_empty():
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("trigger")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("foo")
        await h.press("enter")
        await h.wait_for(lambda: h.capture_queued() == ["foo"])
        await h.press("up")
        await h.wait_input_equals("foo")
        assert h.capture_queued() == []


async def test_queue_delivered_as_next_turn_when_agent_finishes():
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("queued")
        await h.press("enter")
        # Let the agent finish → queued message becomes the next respond()
        agent.block.set()
        await h.wait_for(lambda: agent.messages_received == ["first", "queued"])


async def test_interleaved_cmd_msg_msg_commands_fire_immediately():
    """Per-turn dispatcher model: commands dispatch immediately; only
    messages queue (and consecutive ones merge).

    Contrast with the old contract: messages and commands queued
    together, flushed in order after respond() returned. Now
    commands sidestep the agent's input queue and fire as soon as
    typed; consecutive queued messages compose a single multi-line
    item via the dispatcher's submit-merge.
    """
    agent = _blocking_agent()
    commands_seen: list[str] = []
    async with TUIHarness(agent=agent) as h:
        h.app._on_command = commands_seen.append
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        # Interleave: message → command → message.
        await h.submit_async("queued-text-1")
        await h.submit_async("/my-cmd")
        await h.submit_async("queued-text-2")
        # Two queued messages around a slash command merge into one
        # multi-line queue item — the slash didn't break the chain
        # because slash commands never touch the user_messages queue.
        await h.wait_for(lambda: h.capture_queued() == ["queued-text-1\nqueued-text-2"])
        # The command fired immediately, without waiting for the agent.
        assert commands_seen == ["/my-cmd"]
        # Let the agent pump the rest.
        agent.block.set()
        await h.wait_for(
            lambda: (
                agent.messages_received
                == [
                    "first",
                    "queued-text-1\nqueued-text-2",
                ]
            )
        )


async def test_queue_esc_soft_cancels_and_delivers_queue():
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("queued")
        await h.press("enter")
        await h.press("escape")
        await h.wait_for(lambda: agent.messages_received == ["first", "queued"])


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ Tier 5 — hard cases                                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝


async def test_hard_ctrl_c_interrupts_and_preserves_buffer():
    """C-c while the agent is working cancels the agent but keeps the buffer."""
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("in-progress")
        await h.press("c-c")
        # Agent gets cancelled; buffer contents survive.
        await h.wait_for(lambda: not h.app.is_thinking())
        assert h.capture_input() == "in-progress"


async def test_hard_agent_error_shown_in_output():
    agent = FakeAgent()

    async def step(_self, _msg):
        raise RuntimeError("boom")

    agent.queue(step)
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("go")
        await h.wait_output_contains("boom")


async def test_hard_rich_ansi_preserved_in_output():
    agent = FakeAgent()

    async def step(self: FakeAgent, _msg):
        self.emit_message("**bold**")  # Rich markdown renders bold ANSI

    agent.queue(step)
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("go")
        await h.wait_for(lambda: "\x1b[" in h.capture_output_ansi())


async def test_hard_spinner_and_session_label_in_status():
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        h.app.set_session_label("session-abc")
        await h.submit_async("go")
        await h.wait_for(
            lambda: "thinking" in h.capture_status() and "session-abc" in h.capture_status()
        )


async def test_hard_terminal_resize_does_not_crash():
    async with TUIHarness() as h:
        h.app.handle_resize(cols=40, rows=20)
        await h.type_keys("still works")
        await h.wait_input_equals("still works")


async def test_hard_keystroke_during_agent_finish_not_lost():
    """THE BUG — Plan-C's reason for being.

    User presses a key in the tiny window while the agent is finishing
    and the app would normally be restarting its prompt. In the one-App
    architecture the input buffer is *always* reading, so the key lands.
    """
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        # Simulate the exact race: release the agent and type in the
        # same event-loop tick.
        agent.block.set()
        await h.type_keys("x")
        # The character MUST have landed in the input buffer for the
        # next turn — not dropped, not delivered to a dead typeahead.
        await h.wait_input_equals("x")


async def test_hard_synchronous_commands_dispatch_without_queueing():
    """N synchronous /cmd items typed while agent is working fire immediately.

    Forever-loop contract: commands don't queue behind the agent — the
    agent loop doesn't own them. Each slash command goes straight
    through ``on_command`` as typed; the queue only holds user messages.
    """
    agent = _blocking_agent()
    commands_seen: list[str] = []
    async with TUIHarness(agent=agent) as h:
        h.app._on_command = commands_seen.append
        await h.submit_async("first")  # starts agent; agent blocks
        await h.wait_for(lambda: h.app.is_thinking())
        for i in range(5):
            await h.submit_async(f"/cmd-{i}")
        # Commands all dispatched immediately — none queued.
        assert h.capture_queued() == []
        await h.wait_for(lambda: commands_seen == [f"/cmd-{i}" for i in range(5)])


async def test_hard_sync_on_command_raising_surfaces_to_output():
    """A synchronous ``on_command`` that raises must surface a
    ``[callback error]`` line to scrollback.

    Forever-loop contract: commands no longer queue behind the agent,
    so each failing command reports independently as typed. There's no
    "abort the queue" shortcut any more — there is no command queue.
    """
    agent = _blocking_agent()

    def _raising(_text: str) -> None:
        raise RuntimeError("boom-sync")

    async with TUIHarness(agent=agent) as h:
        h.app._on_command = _raising
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.submit_async("/will-fail")
        await h.wait_for(lambda: "[callback error] RuntimeError: boom-sync" in h.capture_output())


async def test_hard_async_on_command_raising_surfaces_to_output() -> None:
    """An async ``on_command`` coroutine that raises must surface the
    error to scrollback via the task's done-callback (not vanish into
    asyncio's default exception handler)."""
    agent = _blocking_agent()

    async def _raising_async(_text: str) -> None:
        raise RuntimeError("boom-async")

    async with TUIHarness(agent=agent) as h:
        h.app._on_command = _raising_async
        await h.submit_async("/go")
        await h.wait_output_contains("[callback error] RuntimeError: boom-async")


async def test_hard_ctrl_c_emits_interrupted_notice_to_scrollback() -> None:
    """Ctrl-C during an agent turn must put a visible ``✗ Interrupted.``
    marker into scrollback so the user knows the cancellation landed —
    not just silently end the turn."""
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.press("c-c")
        await h.wait_output_contains("Interrupted")


async def test_hard_submit_message_re_entry_pushes_to_queue_not_stomps_task() -> None:
    """Programmatic ``submit_message`` while the forever-loop agent is
    running must push onto the agent's ``user_messages`` queue, not
    replace ``_agent_task``.
    """
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("first")
        await h.wait_for(lambda: h.app.is_thinking())
        assert h.app._agent_task is not None
        first_task = h.app._agent_task

        # Programmatic submission during the blocked turn — pushes onto
        # the hidden InputQueue without replacing _agent_task. The
        # exact tail item depends on whether the dispatcher already
        # consumed "first" (race) — but "second" must be present in
        # whatever the queue currently holds.
        h.app.submit_message("second")
        assert h.app._agent_task is first_task  # not replaced
        snap = agent._user_messages_in.snapshot()
        assert any("second" in item for item in snap), (
            f"expected 'second' to be queued; snapshot={snap!r}"
        )

        # Pump: release the agent so it drains the queue.
        agent.block.set()
        # "second" must reach the agent — either as its own item (if
        # the dispatcher already consumed "first" before the second
        # submit_message ran) or merged into a "first\nsecond" item
        # (if the merge race went the other way).
        await h.wait_for(lambda: any("second" in m for m in agent.messages_received))
