# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import difflib
import logging
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nooa.config.tool_configs import BashConfig

logger = logging.getLogger(__name__)


@dataclass
class BashResult:
    """Result from a bash command execution."""

    stdout: str
    stderr: str
    return_code: int
    sandboxed: bool = False

    def __str__(self) -> str:
        """Format result for display."""
        output = self.stdout
        if self.stderr:
            output += f"\n[stderr]\n{self.stderr}"
        if self.return_code != 0:
            output += f"\n[exit code: {self.return_code}]"
        if self.sandboxed:
            output += "\n[sandboxed]"
        return output

    @property
    def success(self) -> bool:
        """Check if command succeeded."""
        return self.return_code == 0


class BashTool:
    """Execute shell commands with timeout, output capture, and optional SRT sandbox.

    This tool allows the agent to run shell commands safely with:
    - Configurable timeout (prevents hangs)
    - Captured stdout/stderr
    - Working directory control
    - Optional SRT sandbox for security (filesystem/network restrictions)

    Usage in agent code:
        result = await self.bash.run("ls -la")
        result = await self.bash.run("git status", timeout=10)
    """

    def __init__(
        self,
        working_dir: str | Path = ".",
        config: "BashConfig | None" = None,
    ) -> None:
        """Initialize bash tool.

        Args:
            working_dir: Directory to run commands in (per-instance, not in config)
            config: BashConfig instance. Use BashConfig(field=value) to override defaults.
        """
        from nooa.config.tool_configs import BashConfig as _BC

        self.working_dir = Path(working_dir).resolve()
        self.config = config or _BC()

        srt_executable = self.config.srt_executable
        if srt_executable:
            try:
                if "~" in srt_executable:
                    self._srt_path = Path(srt_executable).expanduser().as_posix()
                else:
                    self._srt_path = srt_executable
            except Exception:
                self._srt_path = srt_executable
        else:
            self._srt_path = "srt"
        self._srt_available = self._check_srt_available()
        self.use_sandbox = self.config.use_sandbox
        if self.use_sandbox and not self._srt_available:
            logger.warning("SRT is not available, falling back to unsandboxed execution")

    def _check_srt_available(self) -> bool:
        """Check if SRT sandbox runtime is available and functional."""
        # Verify it can at least show help (confirms it's the right tool)
        try:
            result = subprocess.run(
                [self._srt_path, "--help"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            help_text = result.stdout + result.stderr
            # Must support --settings and not be subtitle editor
            return (
                "--settings" in help_text
                and "shift" not in help_text.lower()  # Not subtitle editor
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return False

    def _wrap_with_srt(self, command: str) -> str:
        """Wrap a command with SRT sandbox."""
        srt_cmd = self._srt_path or "srt"
        if self.config.srt_settings:
            return f"{shlex.quote(srt_cmd)} --settings {shlex.quote(str(self.config.srt_settings))} {shlex.quote(command)}"
        return f"{shlex.quote(srt_cmd)} {shlex.quote(command)}"

    async def run(
        self,
        command: str,
        timeout: float | None = None,
        working_dir: str | Path | None = None,
    ) -> BashResult:
        """Run a shell command and return the result.

        Args:
            command: The shell command to execute
            timeout: Optional timeout override (default: 30s)
            working_dir: Optional working directory override

        Returns:
            BashResult with stdout, stderr, and return code

        Example:
            result = await self.bash.run("ls -la")
            if result.success:
                print(result.stdout)
        """
        timeout = timeout or self.config.default_timeout
        cwd = Path(working_dir).resolve() if working_dir else self.working_dir

        # Determine if we should sandbox this command
        sandboxed = self.use_sandbox and self._srt_available

        # Wrap command with SRT if sandboxing
        if sandboxed:
            exec_command = self._wrap_with_srt(command)
        else:
            exec_command = command

        proc = None
        try:
            # start_new_session puts the shell and all its children into a
            # dedicated process group (PGID == shell PID).  On timeout we
            # killpg the whole group instead of just the shell — otherwise
            # pipeline children survive, keep pipe FDs open, and
            # proc.wait() blocks forever.
            proc = await asyncio.create_subprocess_shell(
                exec_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            return BashResult(
                stdout=stdout_bytes.decode("utf-8", errors="replace").strip(),
                stderr=stderr_bytes.decode("utf-8", errors="replace").strip(),
                return_code=proc.returncode or 0,
                sandboxed=sandboxed,
            )

        except TimeoutError:
            if proc is not None:
                # Kill the entire process group so pipeline children die too.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    # Fallback: start_new_session may not have taken.
                    try:
                        proc.kill()
                    except Exception:
                        pass
                # Close pipe transports so asyncio's subprocess transport
                # sees all pipes disconnected and _try_finish() resolves
                # the wait() future.  Without this, a killed child that
                # held stderr open leaves the transport stuck forever.
                transport = getattr(proc, "_transport", None)
                if transport is not None:
                    try:
                        for proto in getattr(transport, "_pipes", {}).values():
                            if proto.pipe is not None and not proto.pipe.is_closing():
                                proto.pipe.close()
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    pass

            return BashResult(
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                return_code=-1,
                sandboxed=sandboxed,
            )

        except Exception as e:
            return BashResult(
                stdout="",
                stderr=f"Failed to execute command: {e}",
                return_code=-1,
                sandboxed=sandboxed,
            )

    @property
    def sandbox_available(self) -> bool:
        """Check if SRT sandbox is available."""
        return self._srt_available

    def __repr__(self) -> str:
        sandbox_status = "enabled" if self.use_sandbox and self._srt_available else "disabled"
        return f"BashTool(working_dir={self.working_dir!r}, sandbox={sandbox_status})"


@dataclass
class FileResult(BashResult):
    """Result from a file operation.

    Provides file-specific convenience properties. Uses BashResult.__str__()
    for formatting (includes stdout, stderr, return code, and sandboxed status).
    """

    @property
    def lines(self) -> list[str]:
        """Get stdout split into lines, filtering empty lines."""
        return [line for line in self.stdout.split("\n") if line]


class FileTool:
    """File operations using BashTool.

    Usage in agent code:
        result = await self.files.read("src/main.py")
        content = result.stdout  # Get file contents

        result = await self.files.write("output.txt", "Hello, world!")
        print(result.stdout)  # Get confirmation message

        result = await self.files.list("src/")
        files = result.lines  # Get list of filenames
    """

    def __init__(self, bash: BashTool):
        """Initialize with a BashTool for sandboxed execution.

        Args:
            bash: BashTool instance for command execution
        """
        self.bash = bash
        self._rg_available = self._check_rg_available()

    @staticmethod
    def _check_rg_available() -> bool:
        """Check if ripgrep (rg) is installed and functional."""
        try:
            result = subprocess.run(
                ["rg", "--version"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                return True
            logger.warning(
                "ripgrep (rg) exited with code %d; falling back to find/grep", result.returncode
            )
            return False
        except FileNotFoundError:
            logger.warning("ripgrep (rg) not found; falling back to find/grep")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("ripgrep (rg) timed out; falling back to find/grep")
            return False
        except Exception:
            logger.warning("ripgrep (rg) check failed; falling back to find/grep", exc_info=True)
            return False

    async def read(
        self, path: str, start_line: int | None = None, end_line: int | None = None
    ) -> FileResult:
        """Read file contents.

        Args:
            path: File path to read
            start_line: Optional starting line number (1-indexed)
            end_line: Optional ending line number (inclusive)

        Returns:
            FileResult with file contents in stdout

        Example:
            result = await self.files.read("src/main.py")
            content = result.stdout  # Get file contents
        """
        if start_line is not None and end_line is not None:
            cmd = f"sed -n '{start_line},{end_line}p' {shlex.quote(path)}"
        elif start_line is not None:
            cmd = f"tail -n +{start_line} {shlex.quote(path)}"
        else:
            cmd = f"cat {shlex.quote(path)}"

        result = await self.bash.run(cmd)
        if not result.success:
            raise FileNotFoundError(f"Failed to read {path}: {result.stderr}")
        return FileResult(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.return_code,
            sandboxed=result.sandboxed,
        )

    async def write(self, path: str, content: str) -> FileResult:
        """Write content to a file.

        Args:
            path: File path to write
            content: Content to write

        Returns:
            FileResult with confirmation message in stdout

        Example:
            result = await self.files.write("output.txt", "Hello, world!")
            print(result.stdout)  # Get confirmation message
        """
        # Create parent directory if needed
        parent = str(Path(path).parent)
        if parent and parent != ".":
            await self.bash.run(f"mkdir -p {shlex.quote(parent)}")

        # Use heredoc for writing (handles multi-line content)
        # Use a unique delimiter to avoid conflicts with content
        delimiter = "EOF_NEMO_OO_WRITE"
        cmd = f"cat > {shlex.quote(path)} << '{delimiter}'\n{content}\n{delimiter}"

        result = await self.bash.run(cmd)
        if not result.success:
            raise OSError(f"Failed to write {path}: {result.stderr}")
        return FileResult(
            stdout=f"Written {len(content)} bytes to {path}",
            stderr=result.stderr,
            return_code=result.return_code,
            sandboxed=result.sandboxed,
        )

    async def edit_file(
        self,
        filepath: str,
        search_block: str,
        replace_block: str,
        syntax_check_timeout: float = 5.0,
    ) -> FileResult:
        """Edit file with search/replace.

        Args:
            filepath: File path to edit
            search_block: Exact string to find (must be unique)
            replace_block: Replacement string
            syntax_check_timeout: Timeout for syntax checking (default: 5s)

        Returns:
            FileResult with confirmation message in stdout

        Example:
            result = await self.files.edit_file("src/main.py", "old_code", "new_code")
            print(result.stdout)  # Get confirmation message
        """
        # Read file
        read_result = await self.read(filepath)
        content = read_result.stdout

        # Check match count
        count = content.count(search_block)
        if count == 0:
            # Try fuzzy matching
            lines, search_lines = content.split("\n"), search_block.split("\n")
            best_ratio, best_match_raw, best_line = 0.0, "", 0

            for i in range(len(lines) - len(search_lines) + 1):
                section = "\n".join(lines[i : i + len(search_lines)])
                ratio = difflib.SequenceMatcher(None, search_block, section).ratio()
                if ratio > best_ratio:
                    best_ratio, best_line = ratio, i + 1
                    best_match_raw = section

            if best_ratio > 0.6:
                error_msg = (
                    f"ERROR: No exact match found. Did you mean (line {best_line}, {best_ratio:.0%} similar)?\n"
                    f"Expected (repr):\n{repr(search_block[:400])}\n"
                    f"Found in file (repr):\n{repr(best_match_raw[:400])}"
                )
                if len(search_block) < 200:
                    diff = list(
                        difflib.unified_diff(
                            search_block.splitlines(keepends=True),
                            best_match_raw.splitlines(keepends=True),
                            lineterm="",
                            fromfile="search_block",
                            tofile="file_content",
                        )
                    )
                    if diff:
                        error_msg += f"\nDiff:\n{chr(10).join(diff[:20])}"
                error_msg += "\nTIP: Check for trailing whitespace, tabs vs spaces, or line endings"
                raise ValueError(error_msg)
            else:
                raise ValueError(f"Search block not found in {filepath}")

        elif count > 1:
            # Find all match locations
            pos = 0
            locations = []
            for _ in range(count):
                pos = content.find(search_block, pos)
                assert pos != -1, "count() guarantees all occurrences exist"
                locations.append(content[:pos].count("\n") + 1)
                pos += len(search_block)
            raise ValueError(
                f"Found {count} matches. Provide more context to make search_block unique.\n"
                f"Match locations: {', '.join(map(str, locations))}"
            )

        # Perform replacement
        new_content = content.replace(search_block, replace_block, 1)

        temp_path = filepath + ".tmp"
        await self.write(temp_path, new_content)

        try:
            if filepath.endswith(".py"):
                result = await self.bash.run(
                    f"python3 -m py_compile {shlex.quote(temp_path)}",
                    timeout=syntax_check_timeout,
                )
                if result.return_code != 0:
                    raise ValueError(f"Edit would introduce syntax errors:\n{result.stderr}")

            result = await self.bash.run(f"mv {shlex.quote(temp_path)} {shlex.quote(filepath)}")
            if not result.success:
                raise OSError(f"Failed to move temp file: {result.stderr}")
        finally:
            if Path(temp_path).exists():
                await self.bash.run(f"rm -f {shlex.quote(temp_path)}")

        line_num = content[: content.find(search_block)].count("\n") + 1
        return FileResult(
            stdout=f"SUCCESS: Replaced 1 occurrence at line {line_num} in {filepath}",
            stderr=result.stderr,
            return_code=result.return_code,
            sandboxed=result.sandboxed,
        )

    async def list(self, path: str = ".") -> FileResult:
        """List files in a directory.

        Args:
            path: Directory path (default: current directory)

        Returns:
            FileResult with file/directory names in stdout (one per line).
            Use .lines property to get as list.

        Example:
            result = await self.files.list("src/")
            files = result.lines  # Get list of filenames
        """
        result = await self.bash.run(f"ls -1 {shlex.quote(path)}")
        if not result.success:
            raise FileNotFoundError(f"Failed to list {path}: {result.stderr}")
        return FileResult(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.return_code,
            sandboxed=result.sandboxed,
        )

    async def exists(self, path: str) -> FileResult:
        """Check if a file or directory exists.

        Args:
            path: Path to check

        Returns:
            FileResult with "yes" in stdout if exists, "no" otherwise.
            Check stdout.strip() == "yes" to get boolean result.
        """
        result = await self.bash.run(f"test -e {shlex.quote(path)} && echo yes || echo no")
        return FileResult(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.return_code,
            sandboxed=result.sandboxed,
        )

    async def find(self, pattern: str, path: str = ".", type: str | None = None) -> FileResult:
        """Find files matching a glob pattern, respecting ``.gitignore``.

        Uses ``rg --files`` under the hood for fast, gitignore-aware
        traversal. Paths under ``.gitignore``'d directories (e.g.
        ``.venv``, ``node_modules``, ``.git``, ``__pycache__``) are
        skipped by default, so this won't hang on a project root the
        way plain ``find .`` does.

        Args:
            pattern: Glob pattern (e.g., "*.py", "test_*.py").
            path: Directory to search in. Defaults to ".".
            type: Optional filter.
                - ``None`` or ``"f"`` (default): files only, via
                  ``rg --files``.
                - ``"d"``, ``"l"``, ``"s"``, ``"c"``: falls back to
                  ``find`` with an explicit ``-prune`` list for
                  ``.git`` / ``.venv`` / ``node_modules`` /
                  ``__pycache__`` / ``.ruff_cache`` /
                  ``.pytest_cache`` so the walk is still bounded.

        Returns:
            FileResult with matching file paths in stdout (one per
            line). Use ``.lines`` to get them as a list.

        Example:
            result = await self.files.find("*.py", "src/")
            py_files = result.lines  # Get list of matching paths
        """
        prune = (
            r"\( -name .git -o -name .venv -o -name node_modules "
            r"-o -name __pycache__ -o -name .ruff_cache "
            r"-o -name .pytest_cache \) -prune -o"
        )
        if type in (None, "f") and self._rg_available:
            cmd = (
                f"rg --files --hidden --no-require-git --glob {shlex.quote(pattern)} "
                f"{shlex.quote(path)}"
            )
        elif type in (None, "f"):
            cmd = f"find {shlex.quote(path)} {prune} -type f -name {shlex.quote(pattern)} -print"
        else:
            cmd = (
                f"find {shlex.quote(path)} {prune} "
                f"-type {shlex.quote(type)} -name {shlex.quote(pattern)} -print"
            )
        result = await self.bash.run(cmd)
        # ``rg --files`` and ``rg`` exit 1 when nothing matches (like
        # grep). That's not an error for this API — callers want an
        # empty list, not a failed FileResult.
        return_code = (
            0 if result.return_code == 1 and not result.stderr.strip() else result.return_code
        )
        return FileResult(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=return_code,
            sandboxed=result.sandboxed,
        )

    async def grep(self, pattern: str, path: str, context: int = 0) -> FileResult:
        """Search for a pattern in a file or directory.

        Uses ``rg -n`` when available for fast, gitignore-aware search.
        Falls back to ``grep -rn`` when rg is not installed.

        Args:
            pattern: Regex pattern to search for.
            path: File OR directory to search.
            context: Number of context lines before/after each match.

        Returns:
            FileResult with matching lines in stdout. Format is
            ``<path>:<line>:<match>`` when ``path`` is a directory,
            ``<line>:<match>`` when ``path`` is a single file.

        Example:
            result = await self.files.grep("def main", "src/")
            matches = result.stdout  # Get matching lines
        """
        ctx_flag = f"-C {context} " if context > 0 else ""
        if self._rg_available:
            cmd = (
                f"rg -n --hidden --no-require-git {ctx_flag}"
                f"{shlex.quote(pattern)} {shlex.quote(path)}"
            )
        else:
            # Exclude common junk directories to approximate rg's gitignore behavior
            excludes = " ".join(
                f"--exclude-dir={d}"
                for d in (
                    ".git",
                    ".venv",
                    "node_modules",
                    "__pycache__",
                    ".ruff_cache",
                    ".pytest_cache",
                )
            )
            cmd = f"grep -rn {excludes} {ctx_flag}{shlex.quote(pattern)} {shlex.quote(path)}"
        result = await self.bash.run(cmd)
        # rg and grep exit 1 when nothing matches — not an error for this API.
        return_code = (
            0 if result.return_code == 1 and not result.stderr.strip() else result.return_code
        )
        return FileResult(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=return_code,
            sandboxed=result.sandboxed,
        )

    def __repr__(self) -> str:
        return f"FileTool(bash={self.bash!r})"
