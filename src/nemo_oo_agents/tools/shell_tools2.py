# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ShellTools2 — the "simple simple" bake-off shell.

A drop-in replacement for ``ShellTools`` with a deliberately tiny surface,
designed around the single biggest failure mode in the trace data: embedding
multi-line shell/C/python payloads inside Python string literals (quoting hell
+ f-string syntax errors), which accounts for ~26% of execute_python errors.

Four methods:

    run(command, *, stdin=None, timeout=30) -> ShellResult   # str-like result
    write_file(path, content)               -> FileWrite     # no shell, no heredoc
    read(path, lines=None)                  -> str           # view, with line gutter
    replace(path, old, new)                 -> FileWrite      # unique-or-error edit

The persistent ``BashSession`` (stateful cd/export/env) is reused unchanged.
``stdin=`` and ``write_file`` remove every reason to write a heredoc or embed a
script inside a quoted string. ``ShellResult`` subclasses ``str`` so
``result[:200]`` / ``"x" in result`` / iteration all work.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Annotated

from nemo_oo_agents.agentdoc import spec
from nemo_oo_agents.skill import Skill
from nemo_oo_agents.tools._bash_session import BashSession
from nemo_oo_agents.tools._results2 import FileWrite, ShellResult
from nemo_oo_agents.tools.shell_tools import (
    _fuzzy_find_unique,
    _normalize_for_fuzzy,
    _strip_line_number_prefixes,
    _unified_diff,
)


class ShellTools2(Skill):
    """Persistent shell + minimal file ops. The "simple" bake-off variant.

    Tools:
        run(command, stdin=, timeout=)  — run a shell command (stateful)
        write_file(path, content)        — create/overwrite a file (no quoting)
        read(path, lines=)               — read a file (numbered)
        replace(path, old, new)          — replace exactly-one occurrence
    """

    __nosnapshot__ = True

    def __init__(self, cwd: str | Path = ".") -> None:
        self._session = BashSession(cwd=cwd)

    @property
    def cwd(self) -> Path:
        """Current working directory of the shell session."""
        return self._session.cwd

    def __repr__(self) -> str:
        return f"ShellTools2(cwd={str(self._session.cwd)!r})"

    async def run(
        self,
        command: Annotated[str, spec(description="Shell command to execute")],
        *,
        stdin: Annotated[
            str | None,
            spec(
                description="Text piped to the command's stdin. Use this instead of a heredoc "
                "(<<EOF) to feed a script/payload to python, patch, etc. — no quoting needed."
            ),
        ] = None,
        timeout: Annotated[float, spec(description="Max seconds before timeout")] = 30.0,
    ) -> ShellResult:
        """Run a shell command in the persistent bash session.

        State persists: ``cd``, ``export``, ``source``, env vars carry over.

        For anything that would need a heredoc — feeding a script to
        ``python``/``patch``/``sh`` — pass it as ``stdin=`` instead. The text
        is delivered byte-exactly via stdin with no shell-quoting concerns::

            await shell.run("python3 -", stdin=my_script)
            await shell.run("patch -p1", stdin=diff_text)

        The result is a ``str`` subclass: ``result[:200]``, ``"FAIL" in result``,
        and ``result.splitlines()`` all work, plus ``.stdout`` / ``.stderr`` /
        ``.returncode`` / ``.success``.
        """
        if stdin is not None:
            b64 = base64.b64encode(stdin.encode()).decode()
            # Materialize stdin to a tempfile and redirect — works whether the
            # command reads stdin as DATA (cat/patch) or its SCRIPT lives elsewhere.
            command = (
                f"__nemo_in=$(mktemp); base64 -d <<<{b64} > $__nemo_in; "
                f"({command}) < $__nemo_in; __nemo_rc=$?; rm -f $__nemo_in; "
                f"( exit $__nemo_rc )"
            )
        stdout, stderr, code, timed_out = await self._session.run_with_timeout_flag(
            command, timeout=timeout
        )
        return ShellResult(stdout=stdout, stderr=stderr, returncode=code, timed_out=timed_out)

    async def write_file(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        content: Annotated[str, spec(description="Full file content")],
    ) -> FileWrite:
        """Create or overwrite a file with ``content``.

        Use this for any new file or full rewrite — it takes the content as a
        plain Python string, so there is no shell, no heredoc, and no quoting.
        Prefer it over ``run("cat > f <<EOF ...")``.
        """
        resolved = self._resolve(path)
        existed = resolved.exists()
        old = resolved.read_text(errors="replace") if existed else ""
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        diff = _unified_diff(path, old, content) if existed else ""
        return FileWrite(
            path=path,
            created=not existed,
            lines=content.count("\n") + (1 if content else 0),
            diff=diff,
        )

    async def read(
        self,
        path: Annotated[str, spec(description="File path (relative to cwd)")],
        lines: Annotated[
            tuple[int, int] | None,
            spec(description="(start, end) line range, 1-indexed inclusive. None = whole file."),
        ] = None,
    ) -> str:
        """Read a file, returning its text with a ``N| `` line-number gutter.

        Pass ``lines=(start, end)`` for a 1-indexed inclusive range.
        """
        resolved = self._resolve(path)
        if not resolved.is_file():
            return f"File not found: {path}"
        all_lines = resolved.read_text(errors="replace").splitlines()
        total = len(all_lines)
        if lines is None:
            start, end = 1, total
        else:
            start, end = lines
            start = max(1, start)
            end = min(total, end)
        width = len(str(end))
        out = [f"[{path}] lines {start}-{end} of {total}"]
        for i in range(start, end + 1):
            out.append(f"{i:>{width}}| {all_lines[i - 1]}")
        return "\n".join(out)

    async def replace(
        self,
        path: Annotated[str, spec(description="File to edit (relative to cwd)")],
        old: Annotated[
            str,
            spec(description="Text to replace. Must match EXACTLY ONE place or this errors."),
        ],
        new: Annotated[str, spec(description="Replacement text (use '' to delete)")],
    ) -> FileWrite:
        """Replace ``old`` with ``new`` in ``path`` — unique-or-error.

        Unlike a fuzzy replace, this refuses to guess. If ``old`` matches zero
        or multiple places, it errors and tells you how many — narrow ``old``
        with more surrounding context until it is unique. ``new=""`` deletes.
        """
        resolved = self._resolve(path)
        if not resolved.is_file():
            return FileWrite(path=path, success=False, error=f"File not found: {path}")
        content = resolved.read_text(errors="replace")
        old = _strip_line_number_prefixes(old)

        count = content.count(old)
        if count == 1:
            new_content = content.replace(old, new, 1)
        elif count == 0:
            fuzzy = _fuzzy_find_unique(content, old)
            if fuzzy is None:
                return FileWrite(
                    path=path,
                    success=False,
                    error=f"`old` not found in {path}. Check whitespace/indentation, "
                    "or read() the region and copy it exactly.",
                )
            norm_content = _normalize_for_fuzzy(content)
            idx, length = fuzzy
            # Map normalized index back to original by matching line offset.
            prefix_lines = norm_content[:idx].count("\n")
            orig_lines = content.split("\n")
            matched_lines = _normalize_for_fuzzy(old).split("\n")
            n = len(matched_lines)
            original_old = "\n".join(orig_lines[prefix_lines : prefix_lines + n])
            new_content = content.replace(original_old, new, 1)
        else:
            return FileWrite(
                path=path,
                success=False,
                error=f"`old` matched {count} places in {path} — ambiguous. "
                "Add surrounding lines to `old` so it is unique.",
            )

        resolved.write_text(new_content)
        return FileWrite(
            path=path,
            lines=new_content.count("\n") + 1,
            diff=_unified_diff(path, content, new_content),
        )

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self._session.cwd / p)
