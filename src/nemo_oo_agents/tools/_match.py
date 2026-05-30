# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Match — a structured, line-anchored search hit for ShellTools3.

The whole point of Shell3's edit flow: searching returns ``Match`` objects that
carry an *exact* location (path + byte span + line span). ``replace(match, new)``
edits that anchor directly — it never re-searches the file, so "this matched two
places the agent didn't intend" is structurally impossible. The agent inspects
the matches (rendered, optionally with line numbers) and picks before mutating.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Match:
    """A single search hit anchored to an exact location in a file.

    Two replace granularities are available off the same object:

    * ``replace(match, new)``      — replaces the match's *line region*
                                     (``line_range``). This is the common case
                                     (median real edit ≈ 4 lines).
    * ``replace(match.span, new)`` — replaces just the matched *byte span*
                                     (the precise-rename case).

    Render helpers:
        ``match.text``     — the matched line(s), no gutter
        ``match.numbered`` — same, with a ``  42| `` line-number gutter
    """

    path: str
    line: int  # 1-indexed start line of the region
    end_line: int  # 1-indexed end line of the region (inclusive)
    col: int  # 1-indexed column of the matched token on `line`
    byte_start: int  # absolute byte offset of the matched token
    byte_end: int  # absolute byte offset just past the matched token
    matched: str  # the exact matched token text
    _file_lines: tuple[str, ...]  # all lines of the file (no trailing newline), for rendering

    @property
    def line_no(self) -> int:
        """Alias for ``line`` — the 1-indexed start line of the match region."""
        return self.line

    @property
    def line_number(self) -> int:
        """Alias for ``line`` — the name models reach for first."""
        return self.line

    @property
    def line_range(self) -> tuple[int, int]:
        """The (start, end) 1-indexed inclusive line region this Match covers.

        Named ``line_range`` (not ``lines``) so it can't be confused with the
        scalar ``line``/``line_no`` start line.
        """
        return (self.line, self.end_line)

    @property
    def span(self) -> Span:
        """The exact byte span of the matched token (for surgical replaces)."""
        return Span(self.path, self.byte_start, self.byte_end, self.matched)

    @property
    def text(self) -> str:
        """The matched line(s), no line-number gutter."""
        return "\n".join(self._file_lines[self.line - 1 : self.end_line])

    @property
    def numbered(self) -> str:
        """The matched line(s) WITH a ``  N| `` line-number gutter."""
        width = len(str(self.end_line))
        return "\n".join(
            f"{i:>{width}}| {self._file_lines[i - 1]}" for i in range(self.line, self.end_line + 1)
        )

    def pipe(self):
        """Stream this region's lines (no gutter) into a chainable pyp ``Stream``.

        Lets a located region feed pyp transforms/sinks, symmetric with the
        other shell sources::

            await shell.lines("f.py", 5, 7).pipe().grep("x").collect()
        """
        from nemo_oo_agents_cli.tools.pyp import items as _pyp_items

        return _pyp_items(self._file_lines[self.line - 1 : self.end_line])

    def context(self, before: int = 3, after: int = 3) -> Match:
        """Return a new Match widened by `before`/`after` lines (clamped to file)."""
        new_start = max(1, self.line - before)
        new_end = min(len(self._file_lines), self.end_line + after)
        return Match(
            path=self.path,
            line=new_start,
            end_line=new_end,
            col=self.col,
            byte_start=self.byte_start,
            byte_end=self.byte_end,
            matched=self.matched,
            _file_lines=self._file_lines,
        )

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.matched}"


@dataclass
class Span:
    """An exact byte span within a file — the surgical replace target."""

    path: str
    byte_start: int
    byte_end: int
    matched: str

    def __str__(self) -> str:
        return f"{self.path}[{self.byte_start}:{self.byte_end}]"
