# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Type-ahead queue state — pure data, no prompt_toolkit coupling.

``QueueState`` holds what the user has typed while the agent is working.
``render_prompt`` converts it into a prompt_toolkit ``FormattedText`` used as
the dynamic prompt prefix.

Layout the prefix produces (top → bottom):

    ⠋ thinking...          ← present only while the agent is running
    │ queued message line
    │ /queued-command
    ❯                      ← the input cursor lives immediately after this

Queued text is NEVER printed to stdout; it only lives in the dynamic prefix
region, so the terminal scrollback stays clean and Up-arrow can cleanly pop
items back into the input buffer without having to un-print anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prompt_toolkit.formatted_text import FormattedText

from .theme import COLORS


@dataclass
class QueueState:
    """State for the type-ahead queue rendered in the dynamic prompt prefix.

    Messages are combined into a single multi-line block (successive plain
    Enters append to the last item with ``\\n``). Commands are kept as
    separate items so the session dispatches them individually after the
    agent turn.

    ``cancel_requested`` is set by the Esc keybinding: a soft cancel that
    the session reads after ``typeahead_loop`` returns — it cancels the
    current agent call and delivers any queued messages as the next
    ``respond()``.
    """

    messages: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    thinking: bool = False
    thinking_message: str = "thinking..."
    spinner_frame: str = "⠋"
    cancel_requested: bool = False

    def submit(self, text: str) -> None:
        """Process one line submitted via Enter."""
        text = text.rstrip("\n")
        if not text.strip():
            return
        if text.startswith("/") or text.startswith("!"):
            self.commands.append(text)
            return
        if self.messages:
            self.messages[-1] = self.messages[-1] + "\n" + text
        else:
            self.messages.append(text)

    def pop_last_for_edit(self) -> str | None:
        """Pop the most recently queued item (command preferred) for editing."""
        if self.commands:
            return self.commands.pop()
        if self.messages:
            return self.messages.pop()
        return None

    def clear(self) -> None:
        self.messages.clear()
        self.commands.clear()

    @property
    def is_empty(self) -> bool:
        return not self.messages and not self.commands

    def as_joined_messages(self) -> str:
        """Concatenate all queued messages for delivery to ``agent.respond()``."""
        return "\n\n".join(self.messages)

    def as_pending_text(self) -> str:
        """All queued text (messages + commands) for ``_pending_input`` on interrupt."""
        return "\n\n".join(self.messages + self.commands)


def render_prompt(state: QueueState, prompt_char: str = "❯ ") -> FormattedText:
    """Render the dynamic prompt prefix from queue state.

    Order is fixed: spinner line (if thinking) → queued messages → queued
    commands → prompt char. The prompt char has no trailing newline so
    prompt_toolkit draws the input buffer immediately after it.
    """
    spinner_style = f"{COLORS['overlay1']} italic"
    queued_style = f"{COLORS['overlay1']}"
    prompt_style = COLORS["green"]

    fragments: list[tuple[str, str]] = []

    if state.thinking:
        fragments.append((spinner_style, f"{state.spinner_frame} {state.thinking_message}\n"))

    for message in state.messages:
        for line in message.split("\n"):
            fragments.append((queued_style, f"│ {line}\n"))

    for command in state.commands:
        fragments.append((queued_style, f"│ {command}\n"))

    fragments.append((prompt_style, prompt_char))
    return FormattedText(fragments)
