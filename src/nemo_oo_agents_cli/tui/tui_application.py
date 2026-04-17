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
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.processors import BeforeInput

from .queue_state import QueueState

PROMPT_MARKER = "❯ "


class TUIApplication:
    """Owns a single, long-lived ``prompt_toolkit.Application`` for the TUI."""

    def __init__(self, agent: Any = None) -> None:
        self.agent = agent
        self.state = QueueState()

        # Output window: append-only scrollback. Tests read ``.text``; the
        # real impl feeds ANSI-rendered Rich output here.
        self.output_buffer = Buffer(read_only=False)

        # Input window: where user keystrokes land.
        self.input_buffer = Buffer(multiline=True)

        # History — a plain list of submitted strings and a cursor that
        # tracks Up/Down navigation. Simpler than prompt_toolkit's async
        # InMemoryHistory machinery, which requires juggling working_lines
        # and _load_history_task to survive Buffer.reset().
        self._history: list[str] = []
        self._history_cursor: int | None = None

        self._prompt_processor = BeforeInput(PROMPT_MARKER, style="class:prompt")
        self._agent_task: asyncio.Task | None = None

        # Let the FakeAgent (or real agent) push output into our scrollback
        # without knowing about our internals.
        if hasattr(agent, "emit"):
            agent.emit = self.append_output  # type: ignore[attr-defined]

        kb = self._build_key_bindings()

        input_window = Window(
            BufferControl(
                self.input_buffer,
                input_processors=[self._prompt_processor],
            ),
            wrap_lines=True,
        )
        self._input_window = input_window
        self._app = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(
                            BufferControl(self.output_buffer, focusable=False),
                            wrap_lines=True,
                        ),
                        input_window,
                    ]
                ),
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

        # Empty-buffer Up/Down → history navigation. Otherwise
        # prompt_toolkit's default cursor-up/down behaviour takes over.
        empty_buffer = Condition(lambda: self.input_buffer.text == "")

        @kb.add("up", filter=empty_buffer)
        def _(event):
            self._history_navigate(-1)

        @kb.add("down", filter=empty_buffer)
        def _(event):
            self._history_navigate(+1)

        return kb

    # ── submission pipeline -------------------------------------------

    def _on_enter(self) -> None:
        """Bare Enter submits the current buffer to the agent."""
        text = self.input_buffer.text
        if not text.strip():
            self.input_buffer.reset()
            return
        # De-dupe adjacent entries to match shell-history ergonomics.
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_cursor = None
        self.input_buffer.reset()
        self._launch_agent(text)

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

    # ── output pipeline -----------------------------------------------

    def append_output(self, text: str) -> None:
        """Append ``text`` to the output scrollback.

        Accepts plain strings or strings with embedded ANSI. No parsing
        — the terminal renderer handles it.
        """
        if not text:
            return
        existing = self.output_buffer.text
        joined = (
            existing + text if not existing or existing.endswith("\n") else existing + "\n" + text
        )
        self.output_buffer.document = Document(text=joined, cursor_position=len(joined))

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

    def status_text(self) -> str:
        """One-line status area text. Empty until Tier-5 lands."""
        return ""
