# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ShellTools4 — v2 simplicity + Match-based unambiguous edits.

The winner of the shell-tools bake-off: 4 methods, minimal prompt footprint,
and Match objects that eliminate copy-paste ambiguity in edits.

Design rationale (from 20×3 SWE-bench experiment):
- v2's 4-method surface outperformed v3's 9-method surface (30% vs 21.7%)
- Fewer methods = less decision fatigue = smaller diffs = higher pass rate
- Match anchors from read() give unambiguous edit targets without needing
  the full streaming/pipeline infrastructure of v3

Attach to an agent::

    class MyAgent(Agent, llm=llm):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.shell = ShellTools4(cwd="/path/to/repo")
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any

from nemo_oo_agents.agentdoc import spec
from nemo_oo_agents.skill import Skill
from nemo_oo_agents.tools._bash_session import BashSession


class FileWrite:
    """Result of a write/replace operation."""

    def __init__(self, path: str, message: str, diff: str = ""):
        self.path = path
        self.message = message
        self.diff = diff

    def __str__(self) -> str:
        parts = [self.message]
        if self.diff:
            parts.append(self.diff)
        return "\n".join(parts)

    def __repr__(self) -> str:
        return str(self)


class Match:
    """An anchored region of a file — the bridge between reading and editing.

    Print it to view numbered lines. Pass to replace() to edit without ambiguity.
    Slice with [start:end] (1-indexed line numbers) to narrow the region.
    """

    def __init__(self, path: str, start: int, end: int, text: str):
        self._path = path
        self._start = start
        self._end = end
        self._text = text

    @property
    def path(self) -> str:
        """File path."""
        return self._path

    @property
    def start(self) -> int:
        """First line number (1-indexed)."""
        return self._start

    @property
    def end(self) -> int:
        """Last line number (1-indexed, inclusive)."""
        return self._end

    @property
    def text(self) -> str:
        """Raw file content (no line numbers)."""
        return self._text

    @property
    def numbered(self) -> str:
        """Content with line-number gutter."""
        lines = self._text.splitlines(keepends=True)
        width = len(str(self._end))
        numbered = []
        for i, line in enumerate(lines):
            num = self._start + i
            # Strip trailing newline for display, add back
            display = line.rstrip("\n")
            numbered.append(f"{num:>{width}}| {display}")
        return "\n".join(numbered)

    def __str__(self) -> str:
        header = f"[{self._path}] lines {self._start}-{self._end}"
        return f"{header}\n{self.numbered}"

    def __repr__(self) -> str:
        return f"Match({self._path!r}, lines={self._start}-{self._end})"

    def __getitem__(self, key: slice | int) -> "Match":
        """Slice by line number (1-indexed) to narrow the region.

        match[10:20] → lines 10-20 (inclusive)
        match[10]    → single line 10
        """
        lines = self._text.splitlines(keepends=True)

        if isinstance(key, int):
            # Single line
            if key < self._start or key > self._end:
                raise IndexError(f"Line {key} not in range {self._start}-{self._end}")
            idx = key - self._start
            return Match(self._path, key, key, lines[idx])

        if isinstance(key, slice):
            start = key.start if key.start is not None else self._start
            stop = key.stop if key.stop is not None else self._end
            if start < self._start:
                start = self._start
            if stop > self._end:
                stop = self._end
            idx_start = start - self._start
            idx_end = stop - self._start + 1
            text = "".join(lines[idx_start:idx_end])
            return Match(self._path, start, stop, text)

        raise TypeError(f"indices must be int or slice, not {type(key).__name__}")


class ShellResult(str):
    """Result of run() — a str subclass whose VALUE is stdout.

    String operations (``"x" in r``, ``r.splitlines()``, ``r.strip()``) act on
    stdout, so existing code keeps working. But ``repr(r)`` / ``print(r)`` show
    a structured ``BashOutput(...)`` view that surfaces stderr and a non-zero
    return code as named fields — so failures can't be missed (a crashing
    command with empty stdout no longer prints as blank).
    """

    def __new__(cls, stdout: str, stderr: str = "", returncode: int = 0):
        obj = super().__new__(cls, stdout)
        obj.stdout = stdout
        obj.stderr = stderr
        obj.returncode = returncode
        obj.success = returncode == 0
        return obj

    def __repr__(self) -> str:
        parts = [f"stdout={self.stdout!r}"]
        if self.stderr:
            parts.append(f"stderr={self.stderr!r}")
        if self.returncode != 0:
            parts.append(f"return_code={self.returncode}")
        return f"BashOutput({', '.join(parts)})"

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def text(self) -> str:
        """The structured display text (i.e. ``str(self)``)."""
        return self.__repr__()


class ShellTools4(Skill):
    """
    Persistent shell + file ops with unambiguous Match-based edits.

    Core:
        run(command, stdin=, timeout=)  — shell command (state persists)
        read(path, lines=)             — view file/region → Match
        replace(match_or_path, ...)    — edit at Match anchor or by unique string
        write_file(path, content)      — create/overwrite file

    Workflow:
        region = await shell.read("f.py", lines=(10, 25))  # view → Match
        print(region)                                       # numbered lines
        await shell.replace(region, new_code)               # edit (exact anchor)

    Quick edits (no read needed):
        await shell.replace("f.py", "old_text", "new_text")  # must match once

    For search/exploration:
        r = await shell.run("grep -rn 'pattern' src/")
        r = await shell.run("find src -name '*.py'")
        r = await shell.run("pytest tests/ -x")

    Match slicing:
        f = await shell.read("big.py")       # whole file
        print(f[50:80])                       # view lines 50-80
        await shell.replace(f[62:68], fix)    # edit lines 62-68
    """

    def __init__(self, cwd: str = ".", **kwargs: Any):
        super().__init__(**kwargs)
        self.cwd = Path(cwd).resolve()
        self._session: BashSession | None = None

    async def _get_session(self) -> BashSession:
        if self._session is None:
            self._session = BashSession(cwd=str(self.cwd))
            await self._session.start()
        return self._session

    async def run(
        self,
        command: Annotated[str, spec(description="Shell command to execute")],
        *,
        stdin: Annotated[str | None, spec(description="Text piped to stdin (replaces heredocs)")] = None,
        timeout: Annotated[float, spec(description="Max seconds")] = 30.0,
    ) -> ShellResult:
        """
        Run a shell command in the persistent session (cd/env/cwd survive).

        Pass a payload as stdin= instead of heredocs. Result is a str subclass
        with .stdout / .stderr / .returncode / .success.

        Args:
            command: Shell command to execute.
            stdin: Text piped to stdin (no quoting needed).
            timeout: Max seconds before timeout.
        """
        import base64

        if stdin is not None:
            b64 = base64.b64encode(stdin.encode()).decode()
            command = (
                f"__nemo_in=$(mktemp); base64 -d <<<{b64} > $__nemo_in; "
                f"({command}) < $__nemo_in; __nemo_rc=$?; rm -f $__nemo_in; "
                f"( exit $__nemo_rc )"
            )
        session = await self._get_session()
        stdout, stderr, code, timed_out = await session.run_with_timeout_flag(
            command, timeout=timeout
        )
        # Track cwd changes (cd persists in session) for read/replace path resolution
        pwd_out, _, _, _ = await session.run_with_timeout_flag("pwd", timeout=5.0)
        if pwd_out.strip():
            self.cwd = Path(pwd_out.strip())
        return ShellResult(
            stdout=stdout,
            stderr=stderr,
            returncode=code,
        )

    async def read(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        lines: Annotated[tuple[int, int] | None, spec(description="(start, end) 1-indexed inclusive, or None for whole file")] = None,
    ) -> Match:
        """
        Read a file (or line range) → Match object.

        Print the Match to see numbered lines. Pass to replace() for editing.
        Slice with match[start:end] to narrow the region.

        Args:
            path: File path (relative to cwd).
            lines: Optional (start, end) range, 1-indexed inclusive.

        Returns:
            Match with .text, .numbered, .path, .start, .end.
            Sliceable: match[10:20] narrows to lines 10-20.
        """
        resolved = (self.cwd / path).resolve()
        content = resolved.read_text()
        all_lines = content.splitlines(keepends=True)
        total = len(all_lines)

        if lines is not None:
            start, end = lines
            start = max(1, start)
            end = min(total, end)
            text = "".join(all_lines[start - 1 : end])
            return Match(str(path), start, end, text)

        return Match(str(path), 1, total, content)

    async def replace(
        self,
        target: Annotated[Any, spec(description="A Match (from read()) or a file path string")],
        old_or_new: Annotated[str, spec(description="For Match: replacement text. For path: text to find (must be unique)")] = "",
        new: Annotated[str | None, spec(description="For path: replacement text. Leave None for Match.")] = None,
    ) -> FileWrite:
        """
        Edit a file — two forms:

        1. replace(match, new_text) — replace the Match's line region.
        2. replace(path, old, new)  — old must match exactly once. new="" deletes.

        Args:
            target: A Match or file path string.
            old_or_new: For Match: the new text. For path: old text to find.
            new: Only for path form: the replacement text.
        """
        if isinstance(target, Match):
            # Match-based replace
            new_text = old_or_new
            resolved = (self.cwd / target.path).resolve()
            content = resolved.read_text()
            all_lines = content.splitlines(keepends=True)

            # Replace the line range
            before = all_lines[: target.start - 1]
            after = all_lines[target.end :]
            # Ensure new_text ends with newline if original region did
            if new_text and not new_text.endswith("\n") and after:
                new_text += "\n"
            new_content = "".join(before) + new_text + "".join(after)
            resolved.write_text(new_content)

            # Generate diff summary
            diff = f"--- a/{target.path}\n+++ b/{target.path}\n"
            diff += f"@@ -{target.start},{target.end - target.start + 1} @@\n"
            return FileWrite(
                path=target.path,
                message=f"Edited {target.path} (replaced lines {target.start}-{target.end})",
                diff=diff,
            )

        elif isinstance(target, str):
            # String-based replace: replace(path, old, new)
            if new is None:
                raise ValueError(
                    "replace(path, old, new) requires 3 arguments. "
                    "Did you mean replace(match, new_text)?"
                )
            old_text = old_or_new
            resolved = (self.cwd / target).resolve()
            content = resolved.read_text()

            count = content.count(old_text)
            if count == 0:
                raise ValueError(
                    f"old text not found in {target}. "
                    "It must match exactly once — check whitespace and indentation."
                )
            if count > 1:
                raise ValueError(
                    f"old text matched {count} times in {target}. "
                    "It must match exactly once — add surrounding context to make it unique."
                )

            new_content = content.replace(old_text, new, 1)
            resolved.write_text(new_content)

            return FileWrite(
                path=target,
                message=f"Edited {target}",
                diff=f"--- a/{target}\n+++ b/{target}",
            )
        else:
            raise TypeError(f"target must be a Match or file path str, got {type(target).__name__}")

    async def write_file(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        content: Annotated[str, spec(description="Full file content")],
    ) -> FileWrite:
        """
        Create or overwrite a file with content (no shell quoting needed).

        Args:
            path: File path (relative to cwd).
            content: Full file content.
        """
        resolved = (self.cwd / path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return FileWrite(
            path=path,
            message=f"Created {path} ({line_count} lines)",
        )
