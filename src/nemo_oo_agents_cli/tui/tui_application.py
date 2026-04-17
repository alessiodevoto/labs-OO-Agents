# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Single long-lived ``prompt_toolkit.Application`` owning the whole TUI.

This is the "Plan C" rewrite: one Application that holds output
scrollback, the type-ahead queue region, the input buffer, and the
status line. No ``patch_stdout`` and no per-turn ``prompt_async`` —
so no handoff race that drops the first keystroke after the agent
finishes.

Current state: **stub**. Exposes the surface the test harness needs to
drive it; concrete behaviour is built one failing test at a time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl

from .queue_state import QueueState

if TYPE_CHECKING:
    pass


class TUIApplication:
    """Owns a single, long-lived ``prompt_toolkit.Application`` for the TUI."""

    def __init__(self, agent: object | None = None) -> None:
        self.agent = agent
        self.state = QueueState()

        # Output window: append-only scrollback. Tests read ``.text``; the
        # real impl will feed ANSI-rendered Rich output here.
        self.output_buffer = Buffer(read_only=False)  # writable from render path

        # Input window: where user keystrokes land.
        self.input_buffer = Buffer()

        kb = KeyBindings()

        @kb.add("c-c")
        def _(event):
            event.app.exit()

        @kb.add("c-d")
        def _(event):
            event.app.exit()

        input_window = Window(BufferControl(self.input_buffer), height=1)
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

    # ── surface the harness (and real callers) rely on ----------------

    @property
    def is_running(self) -> bool:
        return self._app.is_running

    async def run_async(self) -> None:
        await self._app.run_async()

    def exit(self) -> None:
        if self._app.is_running:
            self._app.exit()

    def status_text(self) -> str:
        """One-line status area text. Empty while the stub is in place."""
        return ""
