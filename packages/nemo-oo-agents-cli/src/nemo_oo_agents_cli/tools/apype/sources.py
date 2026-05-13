"""Stream sources: functions that produce Streams — async-native, non-blocking."""

from __future__ import annotations

import asyncio
import subprocess
import os
import fnmatch as _fnmatch
import re as _re
import sys
from pathlib import Path
from typing import AsyncIterator, Iterable

from nemo_oo_agents_cli.tools.apype.stream import Stream
from nemo_oo_agents_cli.tools.apype.errors import PipeError, make_pipe_error


def cat(*paths: str | Path, encoding: str = "utf-8") -> Stream:
    """
    Read lines from one or more files (non-blocking via thread pool).

    Lines are stripped of trailing newlines.

    Usage:
        cat("file.txt") | grep("pattern")
        cat("a.txt", "b.txt") | sort()
    """

    async def _gen():
        for p in paths:
            path = Path(p)
            content = await asyncio.to_thread(path.read_text, encoding)
            for line in content.splitlines():
                yield line

    return Stream(_gen(), _steps=[f"cat({', '.join(repr(str(p)) for p in paths)})"])


def run(cmd: str, *, check: bool = True, timeout: float = 30.0,
        cwd: str | None = None) -> Stream:
    """
    Run a shell command and stream its stdout lines (non-blocking).

    Uses BashSession under the hood for battle-tested timeout handling,
    stdin isolation, and graceful process cleanup.

    Args:
        cmd: Shell command string.
        check: If True (default), raise PipeError on non-zero exit.
        timeout: Max seconds to wait (default 30s). On timeout, SIGTERM then SIGKILL.
        cwd: Working directory for the command.

    Usage:
        run("ls -la").grep(r"py$")
        run("echo hello", timeout=5).head(1)
    """
    meta = {"returncode": 0, "stderr": "", "cmd": cmd}

    async def _gen():
        from nemo_oo_agents.tools._bash_session import BashSession

        bash = BashSession(cwd=cwd or ".")
        await bash.start()
        try:
            stdout, stderr, returncode = await bash.run(cmd, timeout=timeout)

            meta["returncode"] = returncode
            meta["stderr"] = stderr

            if check and returncode != 0:
                raise make_pipe_error(
                    f"Command failed: {cmd}",
                    cmd=cmd,
                    returncode=returncode,
                    stderr=stderr,
                )

            for line in stdout.splitlines():
                yield line
        finally:
            await bash.close()

    return Stream(_gen(), _steps=[f"run({cmd!r})"], _meta=meta)


def arun(shell_tools, cmd: str, *, timeout: float = 30.0, check: bool = True) -> Stream:
    """
    Stream subprocess output line-by-line as it arrives, using ShellTools.run_stream().

    This is the true streaming source — lines are yielded as the subprocess emits them,
    no buffering of entire output.

    Args:
        shell_tools: A ShellTools instance (e.g. self.shell).
        cmd: Shell command string.
        timeout: Max seconds to wait.
        check: If True, raise PipeError on non-zero exit.

    Usage:
        arun(self.shell, "make test") | grep("FAIL")
        await (arun(self.shell, "tail -f log.txt") | head(100)).collect()
    """

    async def _gen():
        returncode = 0
        async for event in shell_tools.run_stream(cmd, timeout=timeout):
            if hasattr(event, "text"):
                if event.kind == "stdout":
                    for line in event.text.splitlines():
                        yield line
            else:
                returncode = event.returncode
                if check and returncode != 0:
                    raise make_pipe_error(
                        f"Command failed: {cmd}",
                        cmd=cmd,
                        returncode=returncode,
                    )

    return Stream(_gen(), _steps=[f"arun({cmd!r})"])


def find(root: str | Path = ".", *, name: str | None = None, type: str | None = None,
         pattern: str | None = None, exclude: list[str] | None = None,
         max_depth: int | None = None, hidden: bool = False,
         no_ignore: bool = False) -> Stream:
    """
    Walk a directory tree and stream matching paths via ripgrep (non-blocking).

    Uses `rg --files` for fast, .gitignore-aware directory traversal.

    Args:
        root: Starting directory.
        name: Glob pattern for file name matching (e.g. "*.py").
        type: "f" for files only, "d" for dirs only (dirs not supported by rg, falls back to os.walk).
        pattern: Regex pattern to match full path (applied as Python post-filter).
        exclude: Glob patterns to exclude (e.g. ["*.pyc", "vendor/*"]).
        max_depth: Maximum depth to recurse.
        hidden: Search hidden files/dirs (--hidden).
        no_ignore: Don't respect .gitignore (--no-ignore).

    Usage:
        find(".", name="*.py") | grep("test")
        find("src", name="*.rs") | wc()
    """
    regex = _re.compile(pattern) if pattern else None

    # rg --files doesn't list directories; fall back for type="d"
    if type == "d":
        root_path = Path(root)
        exclude_set = set(exclude) if exclude else set()

        def _walk_dirs() -> list[str]:
            results = []
            for dirpath_str, dirnames, _ in os.walk(root_path):
                dirpath = Path(dirpath_str)
                if max_depth is not None:
                    rel = dirpath.relative_to(root_path)
                    if len(rel.parts) > max_depth:
                        dirnames.clear()
                        continue
                if exclude_set:
                    dirnames[:] = [d for d in dirnames if d not in exclude_set]
                for d in dirnames:
                    path_str = str(dirpath / d)
                    if name and not _fnmatch.fnmatch(d, name):
                        continue
                    if regex and not regex.search(path_str):
                        continue
                    results.append(path_str)
            return results

        async def _gen_dirs():
            paths = await asyncio.to_thread(_walk_dirs)
            for p in paths:
                yield p

        return Stream(_gen_dirs(), _steps=[f"find({str(root)!r}, type='d')"])

    # Build rg --files command
    args = ["rg", "--files"]
    if hidden:
        args.append("--hidden")
    if no_ignore:
        args.append("--no-ignore")
    if max_depth is not None:
        args.append(f"--max-depth={max_depth}")
    if name:
        args.append(f"-g{name}")
    if exclude:
        for pat in exclude:
            args.append(f"-g!{pat}")
    args.append(str(root))

    cmd = " ".join(_shell_quote(a) for a in args)

    async def _gen():
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_str = stdout_bytes.decode() if stdout_bytes else ""
        returncode = proc.returncode or 0

        # rg --files returns 1 if no files found — not an error
        if returncode not in (0, 1):
            stderr_str = stderr_bytes.decode() if stderr_bytes else ""
            raise make_pipe_error(
                f"find failed: {cmd}",
                cmd=cmd,
                returncode=returncode,
                stderr=stderr_str,
            )

        for line in stdout_str.splitlines():
            if line:
                if regex and not regex.search(line):
                    continue
                yield line

    return Stream(_gen(), _steps=[f"find({str(root)!r})"])


def glob(pattern: str, *, root: str | Path = ".") -> Stream:
    """
    Glob for files and stream matching paths (non-blocking).

    Usage:
        glob("**/*.py") | grep("test")
    """
    root_path = Path(root)

    def _glob_sync() -> list[str]:
        return [str(p) for p in sorted(root_path.glob(pattern))]

    async def _gen():
        paths = await asyncio.to_thread(_glob_sync)
        for p in paths:
            yield p

    return Stream(_gen(), _steps=[f"glob({pattern!r})"])


def rg(pattern: str, path: str = ".", *, type_filter: str | None = None,
       include: str | None = None, exclude: list[str] | None = None,
       ignore_case: bool = False, fixed: bool = False,
       files_only: bool = False, context: int = 0,
       max_count: int | None = None, hidden: bool = False,
       no_ignore: bool = False) -> Stream:
    """
    Search with ripgrep and stream matching lines (non-blocking).

    Uses the `rg` binary for high-performance regex search across files.
    Respects .gitignore by default.

    Args:
        pattern: Regex pattern (or fixed string if fixed=True).
        path: File or directory to search.
        type_filter: Ripgrep type filter (e.g. "py", "rs", "js").
        include: Glob pattern for files to include (e.g. "*.py").
        exclude: Glob patterns to exclude.
        ignore_case: Case-insensitive search (-i).
        fixed: Fixed string search, not regex (-F).
        files_only: Only output file paths with matches (-l).
        context: Lines of context around each match (-C).
        max_count: Max matches per file (-m).
        hidden: Search hidden files/dirs (--hidden).
        no_ignore: Don't respect .gitignore (--no-ignore).

    Usage:
        rg("TODO", type_filter="py") | cut(fields=[0], sep=":") | sort() | uniq()
        rg("def test_", include="*.py") | wc()
        rg("FIXME", files_only=True) | sort()
    """
    args = ["rg"]
    if ignore_case:
        args.append("-i")
    if fixed:
        args.append("-F")
    if files_only:
        args.append("-l")
    if hidden:
        args.append("--hidden")
    if no_ignore:
        args.append("--no-ignore")
    if context > 0:
        args.append(f"-C{context}")
    if max_count is not None:
        args.append(f"-m{max_count}")
    if type_filter:
        args.append(f"-t{type_filter}")
    if include:
        args.append(f"-g{include}")
    if exclude:
        for pat in exclude:
            args.append(f"-g!{pat}")

    args.append("--")
    args.append(pattern)
    args.append(path)

    cmd = " ".join(_shell_quote(a) for a in args)

    async def _gen():
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_str = stdout_bytes.decode() if stdout_bytes else ""
        stderr_str = stderr_bytes.decode() if stderr_bytes else ""
        returncode = proc.returncode or 0

        # rg returns exit code 1 for "no matches" — not a failure
        if returncode not in (0, 1):
            raise make_pipe_error(
                f"rg failed: {cmd}",
                cmd=cmd,
                returncode=returncode,
                stderr=stderr_str,
            )

        for line in stdout_str.splitlines():
            yield line

    return Stream(_gen(), _steps=[f"rg({pattern!r})"])


def stdin() -> Stream:
    """Read lines from sys.stdin as a stream."""

    async def _gen():
        content = await asyncio.to_thread(sys.stdin.read)
        for line in content.splitlines():
            yield line

    return Stream(_gen(), _steps=["stdin()"])


def lines(text: str) -> Stream:
    """Create a stream from a multiline string."""
    return Stream(iter(text.splitlines()), _steps=["lines(...)"])


def items(iterable: Iterable) -> Stream:
    """Create a stream from any iterable."""
    return Stream(iter(iterable), _steps=["items(...)"])


def empty() -> Stream:
    """Create an empty stream."""
    return Stream(iter([]), _steps=["empty()"])


def seq(start: int = 1, end: int | None = None, *, step: int = 1) -> Stream:
    """
    Generate a numeric sequence (like seq).

    Args:
        start: Start value (inclusive). If end is None, generates 1..start.
        end: End value (inclusive). If None, start is treated as end with start=1.
        step: Step between values.

    Usage:
        seq(5)              # 1, 2, 3, 4, 5
        seq(2, 10, step=2)  # 2, 4, 6, 8, 10
    """
    if end is None:
        actual_start, actual_end = 1, start
    else:
        actual_start, actual_end = start, end

    def _generate():
        i = actual_start
        while i <= actual_end:
            yield str(i)
            i += step

    return Stream(_generate(), _steps=[f"seq({actual_start}, {actual_end})"])


def _shell_quote(s: str) -> str:
    """Simple shell quoting for arguments."""
    if not s:
        return "''"
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./=")
    if all(c in safe_chars for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
