# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Single long-lived ``prompt_toolkit.Application`` owning the whole TUI.

This is the "Plan C" rewrite: one Application that holds output
scrollback, the type-ahead queue region, the input buffer, and the
status line. No ``patch_stdout`` and no per-turn ``prompt_async`` —
so no handoff race that drops the first keystroke after the agent
finishes.

Grown incrementally against the failing tests in
``tests/cli/test_tui_app_behavior.py``. Each method exists because a
behaviour test needed it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput

from .queue_state import QueueState

# CSI + OSC stripper for the plain-text view of output_buffer. We keep
# the original ANSI in _output_ansi; tests that assert on buffer text
# don't want escape sequences in their comparisons.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


PROMPT_MARKER = "❯ "

# Minimal default completions so Tab works on a bare TUIApplication.
# Production wiring replaces this with the real ``CommandRegistry``
# completer — the interface is just ``Completer.get_completions``.
_DEFAULT_COMPLETIONS = ["/help", "/exit", "/clear", "/compact", "!bash", "!ipython"]


class _PrefixCompleter(Completer):
    """Tiny completer: suggests entries from ``candidates`` when the buffer
    starts with ``/`` or ``!`` and is a prefix of a candidate."""

    def __init__(self, candidates: list[str]) -> None:
        self.candidates = candidates

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text or text[0] not in "/!":
            return
        for cand in self.candidates:
            if cand.startswith(text) and cand != text:
                yield Completion(cand, start_position=-len(text))


class TUIApplication:
    """Owns a single, long-lived ``prompt_toolkit.Application`` for the TUI."""

    def __init__(
        self,
        agent: Any = None,
        *,
        on_command: Callable[[str], Awaitable[None] | None] | None = None,
        on_bang: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        """
        Args:
            agent: Object with an async ``respond(user_message)`` method.
            on_command: Called with the raw slash text (e.g. ``"/help"``)
                whenever the user submits one. Session wires this to its
                CommandRegistry. If omitted, commands still land in
                ``commands_dispatched()`` for introspection but nothing
                runs.
            on_bang: Called with the bang body (e.g. ``"echo hi"`` for
                ``!echo hi``). Session wires this to run_in_terminal +
                bash. If omitted, bang commands are only recorded in
                ``last_bang_command()``.
        """
        self.agent = agent
        self._on_command = on_command
        self._on_bang = on_bang
        self.state = QueueState()

        # Output scrollback. Two parallel stores:
        #   * ``output_buffer`` — plain-text view, used by tests and by
        #     callers that want a printable transcript. ANSI stripped.
        #   * ``_output_ansi`` — raw ANSI chunks rendered into the live
        #     TUI via FormattedTextControl so Rich styling survives.
        self.output_buffer = Buffer(read_only=False)
        self._output_ansi: list[str] = []

        # Input window: where user keystrokes land.
        self._completer = _PrefixCompleter(list(_DEFAULT_COMPLETIONS))
        self.input_buffer = Buffer(multiline=True, completer=self._completer)

        # History — a plain list of submitted strings and a cursor that
        # tracks Up/Down navigation. Simpler than prompt_toolkit's async
        # InMemoryHistory machinery, which requires juggling working_lines
        # and _load_history_task to survive Buffer.reset().
        self._history: list[str] = []
        self._history_cursor: int | None = None

        # Command routing. Slash (/foo) items are appended to
        # ``_commands_dispatched``; bang (!foo) items set
        # ``_last_bang_command`` and (in production) run via
        # ``run_in_terminal``. Tests read both via the accessor methods.
        self._commands_dispatched: list[str] = []
        self._last_bang_command: str | None = None

        # Status line fields — surfaced via status_text().
        self._session_label: str = ""
        self._spinner_frame: str = "⠋"

        self._prompt_processor = BeforeInput(PROMPT_MARKER, style="class:prompt")
        self._agent_task: asyncio.Task | None = None

        # Let the FakeAgent (or real agent) push output into our scrollback
        # without knowing about our internals.
        if hasattr(agent, "emit"):
            agent.emit = self.append_output  # type: ignore[attr-defined]

        kb = self._build_key_bindings()

        # Output: render ANSI so Rich styling round-trips through the app.
        # Callable re-evaluates on each draw; we append to _output_ansi.
        def _output_formatted():
            return ANSI("".join(self._output_ansi))

        output_window = Window(
            FormattedTextControl(_output_formatted, focusable=False),
            wrap_lines=True,
        )

        # Queue window: shown only while state.messages / state.commands
        # are non-empty. Mirrors the pre-rewrite ``│ foo`` visual.
        def _queue_formatted():
            lines = []
            for msg in self.state.messages:
                for line in msg.split("\n"):
                    lines.append(("class:queue", f"│ {line}\n"))
            for cmd in self.state.commands:
                lines.append(("class:queue", f"│ {cmd}\n"))
            return lines

        queue_window = ConditionalContainer(
            Window(FormattedTextControl(_queue_formatted, focusable=False), height=None),
            filter=Condition(lambda: bool(self.state.messages or self.state.commands)),
        )

        input_window = Window(
            BufferControl(
                self.input_buffer,
                input_processors=[self._prompt_processor],
            ),
            wrap_lines=True,
        )
        self._input_window = input_window

        # Status line at the bottom — shows spinner + session label.
        def _status_formatted():
            text = self.status_text()
            return [("class:status", text)] if text else []

        status_window = Window(FormattedTextControl(_status_formatted, focusable=False), height=1)

        self._app = Application(
            layout=Layout(
                HSplit([output_window, queue_window, input_window, status_window]),
                focused_element=input_window,
            ),
            key_bindings=kb,
            full_screen=False,
        )

    # ── key bindings --------------------------------------------------

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-c")
        def _(event):
            # If an agent is running, C-c is a hard cancel: kill the
            # task, keep the buffer (user's work in progress survives).
            # Otherwise it exits the app.
            if self.is_thinking() and self._agent_task is not None:
                self._agent_task.cancel()
                return
            event.app.exit()

        @kb.add("c-d")
        def _(event):
            event.app.exit()

        @kb.add("enter")
        def _(event):
            self._on_enter()

        # Alt+Enter (Esc,Enter) and Ctrl+J both insert a newline without
        # submitting. Most terminals map "Shift+Enter" to one of these
        # sequences since true shift+enter has no distinct keycode.
        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _(event):
            event.current_buffer.insert_text("\n")

        # Empty-buffer Up: queue pop wins over history — matches the
        # pre-rewrite typeahead UX (pop the last thing you typed while
        # the agent was working so you can edit it).
        empty_buffer = Condition(lambda: self.input_buffer.text == "")

        @kb.add("up", filter=empty_buffer)
        def _(event):
            popped = self.state.pop_last_for_edit()
            if popped is not None:
                self.input_buffer.text = popped
                self.input_buffer.cursor_position = len(popped)
                return
            self._history_navigate(-1)

        @kb.add("down", filter=empty_buffer)
        def _(event):
            self._history_navigate(+1)

        # Esc: soft-cancel the agent while preserving the queue. Any
        # messages already submitted during the turn are delivered as
        # the next respond() via the done-callback.
        @kb.add("escape")
        def _(event):
            if self.is_thinking() and self._agent_task is not None:
                self._agent_task.cancel()

        return kb

    # ── submission pipeline -------------------------------------------

    def _on_enter(self) -> None:
        """Bare Enter submits the current buffer.

        When the agent is working, the submission is queued instead —
        messages collect in ``state.messages``, slash commands in
        ``state.commands``. Both are flushed when the agent finishes.
        """
        text = self.input_buffer.text
        if not text.strip():
            self.input_buffer.reset()
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_cursor = None
        self.input_buffer.reset()

        if self.is_thinking():
            # QueueState.submit handles the /-vs-message split and appends
            # successive messages with newlines.
            self.state.submit(text)
            return

        if text.startswith("/"):
            self._commands_dispatched.append(text)
            self._fire(self._on_command, text)
            return
        if text.startswith("!"):
            body = text[1:].strip()
            self._last_bang_command = body
            self._fire(self._on_bang, body)
            return

        self._launch_agent(text)

    def _fire(self, cb: Callable[[str], Awaitable[None] | None] | None, arg: str) -> None:
        """Call a user callback; schedule it if it returned a coroutine."""
        if cb is None:
            return
        result = cb(arg)
        if asyncio.iscoroutine(result):
            asyncio.ensure_future(result)

    def _history_navigate(self, direction: int) -> None:
        """Move the history cursor by ``direction`` (-1=older, +1=newer)."""
        if not self._history:
            return
        if self._history_cursor is None:
            if direction < 0:
                self._history_cursor = len(self._history) - 1
            else:
                return
        else:
            new = self._history_cursor + direction
            if new < 0 or new >= len(self._history):
                return
            self._history_cursor = new
        self.input_buffer.text = self._history[self._history_cursor]
        self.input_buffer.cursor_position = len(self.input_buffer.text)

    def _launch_agent(self, user_message: str) -> None:
        """Kick off ``agent.respond(user_message)`` on the event loop."""
        if self.agent is None:
            return
        coro = self.agent.respond(user_message)
        if not asyncio.iscoroutine(coro):
            return
        self._agent_task = asyncio.ensure_future(coro)
        self._agent_task.add_done_callback(self._on_agent_done)

    def _on_agent_done(self, task: asyncio.Task) -> None:
        """Fired once the agent's respond() returns / errors / is cancelled.

        Drains the type-ahead queue: any ``state.messages`` become the
        next agent turn (joined with blank lines); ``state.commands``
        move into ``_commands_dispatched``. If neither is present, we go
        idle — the buffer is already accepting input from the user.
        """
        # Surface errors into output scrollback. Cancellation is not an
        # error (Esc soft-cancel + Ctrl-C both cancel on purpose).
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                self.append_output(f"Agent error: {exc}")

        if self.state.commands:
            for cmd in self.state.commands:
                self._commands_dispatched.append(cmd)
            self.state.commands.clear()

        if self.state.messages:
            joined = self.state.as_joined_messages()
            self.state.messages.clear()
            self._launch_agent(joined)

    # ── output pipeline -----------------------------------------------

    def append_output(self, text: str) -> None:
        """Append ``text`` to the output scrollback.

        ANSI-bearing strings render styled via the output Window's
        FormattedTextControl. The plain ``output_buffer`` keeps a
        stripped copy for tests and transcript consumers.
        """
        if not text:
            return
        self._output_ansi.append(text)
        stripped = _strip_ansi(text)
        existing = self.output_buffer.text
        joined = (
            existing + stripped
            if not existing or existing.endswith("\n")
            else existing + "\n" + stripped
        )
        self.output_buffer.document = Document(text=joined, cursor_position=len(joined))
        if self._app.is_running:
            self._app.invalidate()

    # ── surface the harness (and real callers) rely on ----------------

    @property
    def is_running(self) -> bool:
        return self._app.is_running

    async def run_async(self) -> None:
        await self._app.run_async()

    def exit(self) -> None:
        if self._app.is_running:
            self._app.exit()

    def prompt_char_visible(self) -> bool:
        """True once the prompt-marker processor is attached to the input."""
        return self._prompt_processor is not None

    def input_cursor_position(self) -> int:
        """Current cursor position within the input buffer (0-indexed)."""
        return self.input_buffer.cursor_position

    def is_thinking(self) -> bool:
        """True while an agent task is live (respond() not yet returned)."""
        return self._agent_task is not None and not self._agent_task.done()

    def commands_dispatched(self) -> list[str]:
        """Slash commands the user has submitted, in order."""
        return list(self._commands_dispatched)

    def last_bang_command(self) -> str | None:
        """Most recent ``!shell-command`` the user submitted, or None."""
        return self._last_bang_command

    def completion_candidates(self) -> list[str]:
        """Completion candidates currently offered for the input buffer text."""
        from prompt_toolkit.completion import CompleteEvent

        doc = self.input_buffer.document
        return [
            c.text if c.start_position == 0 else doc.text_before_cursor + c.text
            for c in self._completer.get_completions(doc, CompleteEvent())
        ]

    def status_text(self) -> str:
        """One-line status area text.

        Shows ``<spinner> thinking...`` while the agent is working and a
        bracketed session label when one is set. Example::

            ⠋ thinking...    [session-abc]
        """
        parts: list[str] = []
        if self.is_thinking():
            parts.append(f"{self._spinner_frame} thinking...")
        if self._session_label:
            parts.append(f"[{self._session_label}]")
        return "   ".join(parts)

    def set_session_label(self, label: str) -> None:
        """Set the bracketed label shown on the right of the status line."""
        self._session_label = label

    def handle_resize(self, cols: int, rows: int) -> None:
        """Hint the app to re-layout for a new terminal size.

        prompt_toolkit handles real resize events internally; this hook
        exists for tests (and callers that want to force a redraw after
        a non-SIGWINCH layout change).
        """
        if self._app.is_running:
            self._app.invalidate()
