# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behaviour spec for ``TUIApplication`` (Plan-C, single long-lived app).

Every test here pins a behaviour the old two-prompt TUI had. Right now
the ``TUIApplication`` stub has almost none of these — so every test is
marked ``xfail(strict=True)``. When the real implementation lands in
Phase 2, tests flip green one at a time (``strict=True`` means if a test
starts passing, pytest errors until we drop the mark — preventing silent
drift).

Grouping:

* Tier 1 — baseline REPL
* Tier 2 — input mechanics
* Tier 3 — commands
* Tier 4 — type-ahead queue
* Tier 5 — hard cases (interrupts, rendering, THE BUG)

The tests read logical state (``app.input_buffer.text``,
``app.state.messages``, ``app.status_text()``) via the harness's
``capture_*`` helpers rather than parsing terminal output — same
discipline as the harness canary tests.
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


async def test_queue_multiple_enters_collect_messages():
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("trigger")  # new helper: type+enter+don't wait
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("one")
        await h.press("enter")
        await h.type_keys("two")
        await h.press("enter")
        # Successive Enters append into the same message with newlines
        # (matches QueueState.submit).
        await h.wait_for(lambda: h.capture_queued() == ["one\ntwo"])


async def test_queue_slash_command_while_working_goes_to_commands_slot():
    agent = _blocking_agent()
    async with TUIHarness(agent=agent) as h:
        await h.submit_async("trigger")
        await h.wait_for(lambda: h.app.is_thinking())
        await h.type_keys("/exit")
        await h.press("enter")
        await h.wait_for(lambda: h.app.state.commands == ["/exit"])


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
        await h.wait_for(lambda: "\x1b[" in h.capture_output())


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
