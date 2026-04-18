# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Type-ahead queue state — pure data, no prompt_toolkit coupling.

``QueueState`` holds what the user has typed while the agent is working.
``render_prompt`` converts it into a prompt_toolkit ``FormattedText`` used as
the dynamic prompt prefix.

The queue is a SINGLE ordered list of ``(kind, text)`` items where
``kind`` is ``"msg"`` (agent input) or ``"cmd"`` (slash/bang command).
Preserving submission order lets consumers drain items in the order
the user typed them — interleaving commands and messages correctly
instead of running all commands first then all messages.

Queued text is NEVER printed to stdout; it lives only in the dynamic
prefix region, so terminal scrollback stays clean and Up-arrow can
pop the last item back into the input buffer without un-printing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from prompt_toolkit.formatted_text import FormattedText

from .theme import COLORS

QueueItem = tuple[Literal["msg", "cmd"], str]


@dataclass
class QueueState:
    """State for the type-ahead queue rendered in the dynamic prompt prefix."""

    items: list[QueueItem] = field(default_factory=list)
    thinking: bool = False
    thinking_message: str = "thinking..."
    spinner_frame: str = "⠋"
    cancel_requested: bool = False
    # Carry-over for the first-character-lost band-aid — superseded by
    # the plan-C rewrite but kept so legacy tests still import cleanly.
    unsubmitted_buffer: str = ""

    def submit(self, text: str) -> None:
        """Process one line submitted via Enter.

        Slash/bang lines append as a new ``cmd`` item. Plain-text lines
        either start a new ``msg`` item or — if the tail of the queue
        is already a ``msg`` — extend it with a newline (multi-line
        message continuation).
        """
        text = text.rstrip("\n")
        if not text.strip():
            return
        if text.startswith("/") or text.startswith("!"):
            self.items.append(("cmd", text))
            return
        if self.items and self.items[-1][0] == "msg":
            self.items[-1] = ("msg", self.items[-1][1] + "\n" + text)
        else:
            self.items.append(("msg", text))

    def pop_last_for_edit(self) -> str | None:
        """Pop the most recently queued item for editing in the input
        buffer. Returns the text or ``None`` if the queue is empty."""
        if not self.items:
            return None
        _, text = self.items.pop()
        return text

    def clear(self) -> None:
        self.items.clear()
        self.unsubmitted_buffer = ""

    @property
    def is_empty(self) -> bool:
        return not self.items

    # ── Backward-compatibility accessors ------------------------------
    @property
    def messages(self) -> list[str]:
        """Flat list of message texts (for tests / older call sites)."""
        return [t for k, t in self.items if k == "msg"]

    @property
    def commands(self) -> list[str]:
        """Flat list of slash/bang texts (for tests / older call sites)."""
        return [t for k, t in self.items if k == "cmd"]

    def as_joined_messages(self) -> str:
        """Concatenate queued messages (in order) for delivery as an
        agent turn. Commands are handled separately by the consumer."""
        return "\n\n".join(self.messages)

    def as_pending_text(self) -> str:
        """All queued text in order for ``_pending_input`` on interrupt."""
        return "\n\n".join(t for _, t in self.items)


def render_prompt(state: QueueState, prompt_char: str = "❯ ") -> FormattedText:
    """Render the dynamic prompt prefix from queue state.

    Items render in submission order — a message followed by a command
    followed by another message will show in that exact order in the
    prefix and drain in that order when the agent finishes.
    """
    spinner_style = f"{COLORS['overlay1']} italic"
    queued_style = f"{COLORS['overlay1']}"
    prompt_style = COLORS["green"]

    fragments: list[tuple[str, str]] = []

    if state.thinking:
        fragments.append((spinner_style, f"{state.spinner_frame} {state.thinking_message}\n"))

    for kind, text in state.items:
        if kind == "msg":
            for line in text.split("\n"):
                fragments.append((queued_style, f"│ {line}\n"))
        else:
            fragments.append((queued_style, f"│ {text}\n"))

    fragments.append((prompt_style, prompt_char))
    return FormattedText(fragments)
