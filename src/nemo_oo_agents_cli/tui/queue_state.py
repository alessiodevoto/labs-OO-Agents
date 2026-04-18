# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Type-ahead queue state — pure data, no prompt_toolkit coupling.

``QueueState`` holds items the user submitted while the agent is
working. Items are a single ordered ``list[(kind, text)]`` where
``kind`` is ``"msg"`` or ``"cmd"``. Submission order is preserved so
consumers drain in the sequence the user actually typed — commands
and messages interleaved correctly instead of all-cmds-then-all-msgs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

QueueItem = tuple[Literal["msg", "cmd"], str]


@dataclass
class QueueState:
    """State for the type-ahead queue."""

    items: list[QueueItem] = field(default_factory=list)

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

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def messages(self) -> list[str]:
        """Flat list of message texts, in submission order."""
        return [t for k, t in self.items if k == "msg"]

    @property
    def commands(self) -> list[str]:
        """Flat list of slash/bang texts, in submission order."""
        return [t for k, t in self.items if k == "cmd"]
