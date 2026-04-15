# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""StringIO with hard character limit and truncation notices.

Used by pformat/truncating_pformat to bound memory during formatting of large objects.
"""

import collections
import io


class TruncatingStringIO(io.StringIO):
    """StringIO with hard character limit and head+tail truncation.

    Keeps the first ``head_limit`` chars verbatim and a rolling tail of the
    last ``tail_limit`` chars.  When truncated, ``getvalue()`` returns the
    head and tail joined by a prose notice.

    ``was_truncated`` is True only when ``_chars_written > limit`` (i.e. the
    total content actually exceeded the budget — not merely when the head
    buffer filled up).  When not truncated, ``getvalue()`` returns all content
    verbatim (head concatenated with any tail overflow).

    Example:
        buffer = TruncatingStringIO(limit=100)
        buffer.write("x" * 200)
        content = buffer.getvalue()
        # Returns prose head+tail format with a "chars not shown" notice.
    """

    DEFAULT_LIMIT = 50_000  # 50KB per execution

    def __init__(self, limit: int = DEFAULT_LIMIT, tail_chars: int | None = None):
        """Initialize truncating buffer.

        Args:
            limit: Maximum characters to store (default: 50,000).
            tail_chars: Characters reserved for the tail window.
                        None = half of limit (50/50 split).
        """
        super().__init__()
        self._limit = limit
        self._tail_limit = tail_chars if tail_chars is not None else limit // 2
        self._head_limit = limit - self._tail_limit
        self._head_full = False
        # Rolling tail buffer — each element is a string chunk written after
        # the head was full.  We keep enough chars to reconstruct the last
        # _tail_limit characters.
        self._tail_chunks: collections.deque[str] = collections.deque()
        self._tail_chars = 0
        self._chars_written = 0

    def write(self, s: str) -> int:
        """Write string, filling head then rolling tail."""
        n = len(s)
        self._chars_written += n

        if not self._head_full:
            current_head = len(super().getvalue())
            remaining_head = self._head_limit - current_head

            if n <= remaining_head:
                super().write(s)
                return n

            if remaining_head > 0:
                super().write(s[:remaining_head])
            self._head_full = True
            overflow = s[remaining_head:]
            if overflow:
                self._add_to_tail(overflow)
        else:
            self._add_to_tail(s)

        return n

    def _add_to_tail(self, s: str) -> None:
        """Add a chunk to the rolling tail buffer, evicting old chars as needed."""
        self._tail_chunks.append(s)
        self._tail_chars += len(s)
        while self._tail_chars > self._tail_limit and self._tail_chunks:
            oldest = self._tail_chunks[0]
            excess = self._tail_chars - self._tail_limit
            if len(oldest) <= excess:
                self._tail_chunks.popleft()
                self._tail_chars -= len(oldest)
            else:
                self._tail_chunks[0] = oldest[excess:]
                self._tail_chars -= excess

    def _get_tail(self) -> str:
        """Return the current tail buffer as a single string."""
        return "".join(self._tail_chunks)

    def getvalue(self) -> str:
        """Get buffer contents, with prose head+tail notice if truncated."""
        head = super().getvalue()

        if not self.was_truncated:
            tail = self._get_tail()
            return head + tail

        tail = self._get_tail()
        total = self._chars_written
        head_chars = len(head)
        tail_chars = len(tail)
        dropped = total - head_chars - tail_chars

        return (
            f"<truncated-output>\n"
            f"Output too large ({total:,} chars). "
            f"Showing first {head_chars:,} and last {tail_chars:,} chars.\n\n"
            f"{head}\n\n"
            f"... {dropped:,} chars not shown ...\n\n"
            f"{tail}\n"
            f"</truncated-output>"
        )

    @property
    def was_truncated(self) -> bool:
        """True if total chars written exceeded the limit."""
        return self._chars_written > self._limit

    @property
    def chars_written(self) -> int:
        """Total characters written (including chars that were dropped)."""
        return self._chars_written
