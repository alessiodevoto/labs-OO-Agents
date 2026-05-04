# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ShellTools — persistent shell session with file operations.

Replaces ``BashTool`` and ``FileTool`` with a unified skill that maintains
a persistent bash session.  ``cd``, ``export``, environment variables, and
working directory all survive across calls.

Attach to an agent::

    class MyAgent(Agent, llm=llm):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.shell = ShellTools(cwd="/path/to/repo")

The LLM discovers tools via ``doc(self.shell)`` and sees the current
working directory in ``pprint(self)`` thanks to ``__repr__``.
"""

import difflib
import logging
import shlex
from pathlib import Path

from nemo_oo_agents.skill import Skill
from nemo_oo_agents.tools._bash_session import BashSession
from nemo_oo_agents.tools._results import (
    BashResult,
    EditResult,
    LsResult,
    SearchResult,
    ViewResult,
    WriteResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_VIEW_LINES = 2000
_TRUNCATED_MSG = (
    "\n<response clipped>\n"
    "NOTE: Output truncated. Use grep to search within the file, "
    "or view with offset/limit to see specific sections."
)

# Directories to skip in ls/find
_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        "dist",
        "build",
        ".eggs",
    }
)


class ShellTools(Skill):
    """Persistent shell session with file operations.

    Provides a bash shell where ``cd``, ``export``, and environment changes
    persist across calls, plus file viewing, editing, searching, and
    directory listing.

    Tools:
        bash(command)           — run a shell command (stateful)
        view(path, ...)         — read a file with line numbers
        edit(path, old, new)    — str_replace with lint check
        write(path, content)    — create or overwrite a file
        grep(pattern, path)     — regex search via ripgrep
        find(pattern, path)     — find files by glob
        ls(path, depth)         — tree-structured directory listing
    """

    __nosnapshot__ = True

    def __init__(self, cwd: str | Path = ".") -> None:
        self._session = BashSession(cwd=cwd)
        self._current_file: str | None = None
        self._current_line: int | None = None

    def __repr__(self) -> str:
        parts = [f"cwd={str(self._session.cwd)!r}"]
        if self._current_file:
            loc = self._current_file
            if self._current_line:
                loc += f":{self._current_line}"
            parts.append(f"file={loc!r}")
        return f"ShellTools({', '.join(parts)})"

    # ------------------------------------------------------------------
    # bash
    # ------------------------------------------------------------------
    async def bash(self, command: str, timeout: float = 120.0) -> BashResult:
        """Run a shell command in the persistent bash session.

        State persists: ``cd``, ``export``, ``source``, aliases, and
        environment variables carry over between calls.

        Args:
            command: Shell command to execute.
            timeout: Max seconds to wait (default: 120).

        Returns:
            BashResult with stdout, stderr, return_code, and .text for display.

        Examples:
            result = await self.shell.bash("cd src && ls")
            result = await self.shell.bash("git status")
            result = await self.shell.bash("python -m pytest tests/ -x", timeout=300)
        """
        stdout, stderr, code = await self._session.run(command, timeout=timeout)
        timed_out = code == 124 and not stdout and not stderr
        return BashResult(
            stdout=stdout,
            stderr=stderr,
            return_code=code,
            timed_out=timed_out,
        )

    # ------------------------------------------------------------------
    # view
    # ------------------------------------------------------------------
    async def view(
        self,
        path: str,
        offset: int = 1,
        limit: int = _MAX_VIEW_LINES,
    ) -> ViewResult:
        """Read a file with numbered lines.

        If ``path`` is a directory, returns a tree listing instead.

        Args:
            path: File or directory path (relative to cwd).
            offset: Start line (1-indexed, default: 1).
            limit: Max lines to show (default: 2000).

        Returns:
            ViewResult with numbered content and metadata.

        Examples:
            r = await self.shell.view("src/main.py")
            r = await self.shell.view("src/main.py", offset=50, limit=100)
        """
        resolved = self._resolve(path)

        if resolved.is_dir():
            ls_r = await self.ls(path, depth=2)
            return ViewResult(
                path=path,
                content=ls_r.tree,
                start_line=0,
                end_line=0,
                total_lines=ls_r.num_files,
            )

        if not resolved.is_file():
            return ViewResult(
                path=path,
                content=f"Error: {path} not found",
                start_line=0,
                end_line=0,
                total_lines=0,
            )

        text = resolved.read_text(errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = max(1, offset)
        end = min(total, start + limit - 1)
        selected = lines[start - 1 : end]

        width = len(str(end))
        numbered = "\n".join(f"{i:{width}d}|{line}" for i, line in enumerate(selected, start))

        truncated = end < total
        if truncated:
            numbered += _TRUNCATED_MSG

        self._current_file = path
        self._current_line = start

        return ViewResult(
            path=path,
            content=numbered,
            start_line=start,
            end_line=end,
            total_lines=total,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # edit
    # ------------------------------------------------------------------
    async def edit(self, path: str, old_str: str, new_str: str) -> EditResult:
        """Replace text in a file (str_replace pattern).

        The ``old_str`` must match **exactly one** location in the file.
        After editing, runs a syntax check and reports only NEW errors.

        Args:
            path: File to edit (relative to cwd).
            old_str: Exact text to find (must be unique in the file).
            new_str: Replacement text.

        Returns:
            EditResult with diff, lint errors, and success status.

        Examples:
            r = await self.shell.edit("src/main.py",
                old_str='print("hello")',
                new_str='print("hello world")')
        """
        resolved = self._resolve(path)
        if not resolved.is_file():
            return EditResult(path=path, diff="", success=False, error=f"File not found: {path}")

        content = resolved.read_text(errors="replace")

        count = content.count(old_str)
        if count == 0:
            return EditResult(
                path=path,
                diff="",
                success=False,
                error=(
                    f"old_str not found in {path}. "
                    "Make sure it matches exactly, including whitespace and indentation."
                ),
            )
        if count > 1:
            return EditResult(
                path=path,
                diff="",
                success=False,
                error=(
                    f"old_str found {count} times in {path}. "
                    "It must match exactly once — add surrounding context to make it unique."
                ),
            )

        # Pre-edit lint
        pre_errors = await self._lint(resolved)

        # Apply
        new_content = content.replace(old_str, new_str, 1)
        resolved.write_text(new_content)

        diff = _unified_diff(path, content, new_content)

        # Post-edit lint — report only new errors
        post_errors = await self._lint(resolved)
        new_errors = sorted(set(post_errors) - set(pre_errors))

        # Update current location
        self._current_file = path
        self._current_line = content[: content.index(old_str)].count("\n") + 1

        return EditResult(
            path=path,
            diff=diff,
            lint_errors=new_errors,
            success=True,
        )

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------
    async def write(self, path: str, content: str) -> WriteResult:
        """Create or overwrite a file.

        Creates parent directories if needed.

        Args:
            path: File path (relative to cwd).
            content: Full file content.

        Returns:
            WriteResult with path, created flag, and line count.

        Examples:
            r = await self.shell.write("src/new_module.py",
                                       "def hello():\\n    pass\\n")
        """
        resolved = self._resolve(path)
        created = not resolved.exists()

        diff = ""
        if not created:
            old = resolved.read_text(errors="replace")
            diff = _unified_diff(path, old, content)

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)

        self._current_file = path
        self._current_line = 1

        return WriteResult(
            path=path,
            created=created,
            lines=len(content.splitlines()),
            diff=diff,
        )

    # ------------------------------------------------------------------
    # grep
    # ------------------------------------------------------------------
    async def grep(
        self,
        pattern: str,
        path: str = ".",
        *,
        include: str | None = None,
        context: int = 0,
        literal: bool = False,
        max_matches: int = 100,
    ) -> SearchResult:
        """Search for a regex pattern in files via ripgrep.

        Works on both files and directories, respects ``.gitignore``.

        Args:
            pattern: Regex pattern (or literal if ``literal=True``).
            path: File or directory to search (default: cwd).
            include: Glob filter (e.g. ``"*.py"``).
            context: Lines of context around each match.
            literal: If True, treat pattern as literal string.
            max_matches: Maximum matches to return (default: 100).

        Returns:
            SearchResult with formatted match lines.

        Examples:
            r = await self.shell.grep("def main", "src/")
            r = await self.shell.grep("TODO", ".", include="*.py")
        """
        parts = ["rg", "-n", "--color=never", "--no-heading"]
        if literal:
            parts.append("-F")
        if context > 0:
            parts.extend(["-C", str(context)])
        if include:
            parts.extend(["-g", _sq(include)])
        parts.extend(["-m", str(max_matches), "--", _sq(pattern), _sq(path)])

        stdout, _, code = await self._session.run(" ".join(parts), timeout=30)

        if code == 1:  # rg: no matches
            return SearchResult(matches=[], total_matches=0)

        all_lines = [ln for ln in stdout.splitlines() if ln.strip()] if stdout else []
        if context > 0:
            # With -C, rg inserts "--" separators and context lines.
            # Count only actual match lines (those with line-number ":" separator).
            match_lines = [ln for ln in all_lines if ln != "--"]
            total = len(match_lines)
        else:
            match_lines = all_lines
            total = len(match_lines)
        return SearchResult(
            matches=all_lines[:max_matches],  # include context in output
            total_matches=total,  # but count only matches
            truncated=total >= max_matches,
        )

    # ------------------------------------------------------------------
    # find
    # ------------------------------------------------------------------
    async def find(
        self,
        pattern: str,
        path: str = ".",
        *,
        type: str = "f",
        max_results: int = 200,
    ) -> SearchResult:
        """Find files matching a glob pattern (gitignore-aware).

        Args:
            pattern: Glob pattern (e.g. ``"*.py"``, ``"test_*.py"``).
            path: Directory to search (default: cwd).
            type: ``"f"`` for files (default), ``"d"`` for directories.
            max_results: Maximum results (default: 200).

        Returns:
            SearchResult with matching file paths.

        Examples:
            r = await self.shell.find("*.py", "src/")
            r = await self.shell.find("*.test.ts")
        """
        if type == "d":
            prune = " ".join(f"-not -path '*/{d}/*'" for d in sorted(_IGNORE_DIRS))
            cmd = f"find {_sq(path)} -maxdepth 10 -type d -name {_sq(pattern)} {prune}"
        else:
            cmd = f"rg --files -g {_sq(pattern)} {_sq(path)}"

        stdout, _, _ = await self._session.run(cmd, timeout=30)
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()] if stdout else []
        total = len(lines)
        return SearchResult(
            matches=lines[:max_results],
            total_matches=total,
            truncated=total > max_results,
        )

    # ------------------------------------------------------------------
    # ls
    # ------------------------------------------------------------------
    async def ls(
        self,
        path: str = ".",
        depth: int = 3,
        max_entries: int = 500,
    ) -> LsResult:
        """List directory contents as a tree.

        Args:
            path: Directory to list (default: cwd).
            depth: Maximum depth to recurse (default: 3).
            max_entries: Maximum entries to show (default: 500).

        Returns:
            LsResult with formatted tree output and entry count.

        Examples:
            r = await self.shell.ls()
            r = await self.shell.ls("src/", depth=2)
        """
        resolved = self._resolve(path)
        if not resolved.is_dir():
            return LsResult(path=path, tree=f"Error: {path} is not a directory", num_files=0)

        lines: list[str] = []
        count = 0
        truncated = False

        def _walk(dir_path: Path, prefix: str, cur_depth: int) -> None:
            nonlocal count, truncated
            if truncated or cur_depth > depth:
                return
            try:
                entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except PermissionError:
                return

            entries = [
                e for e in entries if e.name not in _IGNORE_DIRS and not e.name.startswith(".")
            ]

            for i, entry in enumerate(entries):
                if count >= max_entries:
                    truncated = True
                    return
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{prefix}{connector}{entry.name}{suffix}")
                count += 1
                if entry.is_dir():
                    ext = "    " if is_last else "│   "
                    _walk(entry, prefix + ext, cur_depth + 1)

        lines.append(f"{path}/")
        _walk(resolved, "", 1)

        return LsResult(
            path=path,
            tree="\n".join(lines),
            num_files=count,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve(self, path: str) -> Path:
        """Resolve a path relative to the session cwd."""
        p = Path(path)
        return p if p.is_absolute() else self._session.cwd / p

    async def _lint(self, path: Path) -> list[str]:
        """Quick syntax check. Returns list of error strings."""
        errors: list[str] = []
        if path.suffix == ".py":
            stdout, stderr, code = await self._session.run(
                f"python3 -c \"import ast; ast.parse(open('{path}').read())\"",
                timeout=10,
            )
            if code != 0:
                for line in (stderr or stdout).splitlines():
                    if line.strip():
                        errors.append(line.strip())
        return errors

    async def reset(self) -> None:
        """Kill and restart the bash session, preserving the working directory.

        Use when the session is corrupted (e.g. after a timeout caused by
        unbalanced quotes or a stuck interactive process).
        """
        await self._session.reset()

    async def close(self) -> None:
        """Shut down the persistent bash session."""
        await self._session.close()


def _unified_diff(path: str, old: str, new: str) -> str:
    """Generate a unified diff between old and new content."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _sq(s: str) -> str:
    """Shell-quote a string."""
    return shlex.quote(s)
