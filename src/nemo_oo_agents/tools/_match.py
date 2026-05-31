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


@dataclass(repr=False)
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

    def __repr__(self) -> str:
        # Summarize _file_lines so print(match)/print(matches) doesn't dump the
        # whole file (a single Match can otherwise repr to tens of KB). The full
        # text stays reachable via .text / .numbered.
        return (
            f"Match(path={self.path!r}, line={self.line}, end_line={self.end_line}, "
            f"col={self.col}, byte_start={self.byte_start}, byte_end={self.byte_end}, "
            f"matched={self.matched!r}, _file_lines=<{len(self._file_lines)} lines>)"
        )

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
        return self._subregion(self.line - before, self.end_line + after)

    @property
    def lines(self) -> _LineSlicer:
        """Navigate to a narrower sub-region by **1-indexed line number**.

        ``match.lines[680:684]`` returns a new ``Match`` spanning lines 680-683
        (Python slice semantics on line numbers), ready to ``replace()`` directly
        — no hand-built snippet::

            region = await shell.lines("big.py", 1, 999)   # a Match over a range
            await shell.replace(region.lines[680:684], new_text)

        ``match.lines[690]`` returns a single-line sub-Match.
        """
        return _LineSlicer(self)

    def _subregion(self, start: int, end: int) -> Match:
        """Build a Match over lines [start, end] (1-indexed inclusive), same file."""
        n = len(self._file_lines)
        # Clamp both bounds to the file so an out-of-range region (start past EOF)
        # yields an empty Match instead of an IndexError in the byte-offset loop.
        start = max(1, min(start, n + 1))
        end = min(n, end)
        byte_start = sum(len(self._file_lines[i].encode()) + 1 for i in range(start - 1))
        region_text = "\n".join(self._file_lines[start - 1 : end])
        return Match(
            path=self.path,
            line=start,
            end_line=end,
            col=1,
            byte_start=byte_start,
            byte_end=byte_start + len(region_text.encode()),
            matched=region_text,
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


class _LineSlicer:
    """1-indexed line-slicing accessor for ``Match.lines`` -> sub-``Match``."""

    __slots__ = ("_match",)

    def __init__(self, match: Match):
        self._match = match

    def __getitem__(self, key) -> Match:
        n = len(self._match._file_lines)
        if isinstance(key, slice):
            if key.step is not None:
                raise ValueError("Match.lines[...] does not support a slice step")
            start = 1 if key.start is None else key.start
            end = n if key.stop is None else key.stop - 1  # slice stop is exclusive
            if start < 1 or end < 0:
                raise ValueError(
                    "Match.lines[...] uses positive 1-indexed line numbers (no negatives)"
                )
            return self._match._subregion(start, end)
        if isinstance(key, int):
            if key < 1:
                raise ValueError(
                    "Match.lines[i] uses positive 1-indexed line numbers (no negatives)"
                )
            return self._match._subregion(key, key)
        raise TypeError(f"Match.lines[...] expects an int or slice, got {type(key).__name__}")
