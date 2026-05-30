# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Result types for ShellTools2 / ShellTools3 (the bake-off shells).

The headline change vs the original ``RunResult``: ``ShellResult`` subclasses
``str``.  Agents habitually treat a run result as the command's text —
``result[:200]``, ``if "FAIL" in result``, iterating lines.  Making it a real
``str`` means all of that just works, while ``.stdout`` / ``.stderr`` /
``.returncode`` / ``.success`` remain available for code that wants them.
"""

from __future__ import annotations

from dataclasses import dataclass


class ShellResult(str):
    """A command result that *is* its text.

    ``str(result)``, slicing, ``in``, iteration, ``.splitlines()`` all operate
    on the combined display text (stdout, then stderr, then a status line).
    Structured fields are attached as attributes.
    """

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool

    def __new__(
        cls, stdout: str = "", stderr: str = "", returncode: int = 0, timed_out: bool = False
    ) -> ShellResult:
        parts: list[str] = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        if timed_out:
            parts.append("[timed out]")
        elif returncode != 0:
            parts.append(f"[exit code: {returncode}]")
        text = "\n".join(parts) if parts else "(no output)"
        self = super().__new__(cls, text)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timed_out = timed_out
        return self

    @property
    def success(self) -> bool:
        """True when exit code is 0 and the command did not time out."""
        return self.returncode == 0 and not self.timed_out

    @property
    def text(self) -> str:
        """The display text (i.e. ``str(self)``)."""
        return str(self)

    @property
    def lines(self):
        """Pipe the buffered output into a chainable pyp ``Stream`` over its lines.

        Lets a finished ``run()`` result feed the same transforms/sinks as
        ``run_pipe``/``rg``/``cat`` without re-running anything::

            await (await shell.run("make test")).lines.grep("FAIL").collect()

        For the common case prefer the ``|`` operator (see ``__or__``).
        """
        from nemo_oo_agents_cli.tools.pyp import lines as _pyp_lines

        return _pyp_lines(self.stdout)

    def __or__(self, other):
        """Pipe the buffered output like a shell pipe — ``result | other``.

        * ``result | "pattern"`` — grep the lines for ``pattern`` (regex), the
          90% case. Returns a chainable ``Stream``::

              await (await shell.run("ps aux") | "python").collect()

        * ``result | transform`` — apply any pyp transform callable, mirroring
          ``Stream.__or__``::

              await (await shell.run("ls") | (lambda ait: ait)).collect()

        Operates over the buffered stdout (``.lines``), so it is pipe-over-result,
        not live streaming — use ``run_pipe`` for unbounded/live output. Returns
        a ``Stream``; finish with a sink (``.collect()`` / ``.count()`` / ...).
        """
        stream = self.lines
        if isinstance(other, str):
            return stream.grep(other)
        if callable(other):
            return stream | other
        return NotImplemented


@dataclass
class FileWrite:
    """Result of ``write_file`` / ``replace``."""

    path: str
    created: bool = False
    lines: int = 0
    diff: str = ""
    success: bool = True
    error: str = ""

    @property
    def text(self) -> str:
        """Formatted summary for display (success line, or the error)."""
        if not self.success:
            return f"Failed: {self.error}"
        if self.diff:
            return f"Edited {self.path}\n{self.diff}"
        action = "Created" if self.created else "Wrote"
        return f"{action} {self.path} ({self.lines} lines)"

    def __str__(self) -> str:
        return self.text
