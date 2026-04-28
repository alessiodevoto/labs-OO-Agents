# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
SWEBench tool suite for execution inside a Harbor Singularity container.

All commands run via local subprocess — no Docker sidecar or Harbor env proxy
needed. File operations (view/edit/create) use direct Python I/O for speed.
"""

from __future__ import annotations

import asyncio
import secrets
import shlex
from pathlib import Path


class SWEBenchLocalTools:
    """
    SWEBench tools for agents running inside a Harbor Singularity container.

    Harbor pre-activates the testbed conda environment before invoking the
    runner, so commands execute directly without a conda prefix.

    The repository lives at ``/testbed`` by default; pass ``workdir`` to
    override.  Relative paths in file methods are resolved against ``workdir``.
    """

    def __init__(self, workdir: str = "/testbed") -> None:
        self._workdir = workdir

    # ------------------------------------------------------------------
    # Core: shell execution
    # ------------------------------------------------------------------

    async def execute(self, command: str) -> str:
        """Execute a shell command inside the container and return combined output."""
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._workdir,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="replace") if stdout else ""

    # ------------------------------------------------------------------
    # Repository navigation
    # ------------------------------------------------------------------

    async def find_files(self, pattern: str, directory: str = ".") -> str:
        """Find files matching a glob pattern."""
        return await self.execute(
            f"find {shlex.quote(directory)} -name {shlex.quote(pattern)} -type f"
        )

    async def repo_tree(self, directory: str = ".") -> str:
        """Return a depth-3 tree view, skipping caches and build artefacts."""
        return await self.execute(
            f"tree -L 3 -I '__pycache__|*.pyc|.git|.pytest_cache|node_modules|.tox|venv|.venv'"
            f" {directory} | head -200"
        )

    async def view_file(
        self, filepath: str, start_line: int = 1, end_line: int | None = None
    ) -> str:
        """Read a file, optionally limited to a line range."""
        path = self._resolve(filepath)
        lines = path.read_text(errors="replace").splitlines()
        subset = lines[start_line - 1 : end_line]
        return "\n".join(subset)

    # ------------------------------------------------------------------
    # File editing
    # ------------------------------------------------------------------

    async def edit_file(self, filepath: str, search_block: str, replace_block: str) -> str:
        """Exact search-and-replace edit.  Refuses to modify test files."""
        path = self._resolve(filepath)
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            raise ValueError(f"Cannot edit test files: {filepath}")
        content = path.read_text()
        count = content.count(search_block)
        if count == 0:
            raise ValueError(f"search_block not found in {path}")
        if count > 1:
            raise ValueError(f"search_block matches {count} locations in {path} — be more specific")
        path.write_text(content.replace(search_block, replace_block, 1))
        return f"Edited {path} successfully."

    async def create_file(self, filepath: str, content: str) -> str:
        """Create (or overwrite) a file with the given content."""
        path = self._resolve(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"Created {path} successfully."

    # ------------------------------------------------------------------
    # Running code and tests
    # ------------------------------------------------------------------

    async def run_python(self, code: str) -> str:
        """Run a Python snippet in the testbed environment."""
        # Use a random delimiter so LLM-generated code cannot inject a premature
        # end-of-heredoc marker.
        delimiter = f"PYEOF_{secrets.token_hex(8)}"
        return await self.execute(f"python3 << '{delimiter}'\n{code}\n{delimiter}")

    async def run_tests(self, test_path: str | None = None) -> str:
        """Run the repository test suite (or a specific path/pattern)."""
        cmd = f"pytest {test_path} -v" if test_path else "pytest -v"
        return await self.execute(cmd)

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    async def git_diff(self) -> str:
        """Return the diff of all changes relative to HEAD."""
        diff = await self.execute("git diff HEAD")
        return diff if diff.strip() else "No files changed."

    async def git_status(self) -> str:
        """Return git status."""
        return await self.execute("git status")

    # ------------------------------------------------------------------
    # Symbol analysis (grep-based; no Jedi dependency)
    # ------------------------------------------------------------------

    async def find_definition(self, symbol_name: str, search_dir: str = ".") -> str:
        """Find where a class or function is defined (grep-based)."""
        return await self.execute(
            f'grep -rn "\\bdef {symbol_name}\\b\\|\\bclass {symbol_name}\\b"'
            f' {search_dir} --include="*.py" | head -20'
        )

    async def find_references(self, symbol_name: str, search_dir: str = ".") -> str:
        """Find all references to a symbol name (grep-based)."""
        pattern = shlex.quote(f"\\b{symbol_name}\\b")
        return await self.execute(
            f"grep -rn {pattern} {shlex.quote(search_dir)} --include='*.py' | head -30"
        )

    async def find_test_files(self, module_path: str) -> str:
        """Find test files that import the given module."""
        import os

        module_name = os.path.splitext(os.path.basename(module_path))[0]
        found = await self.execute(
            f'grep -rl "import.*{module_name}" --include="test_*.py" --include="*_test.py"'
            f" | head -20"
        )
        if found.strip():
            return f"Test files that import {module_name}:\n{found}"
        return f"No test files found for {module_path}"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(self, filepath: str) -> Path:
        p = Path(filepath)
        return p if p.is_absolute() else Path(self._workdir) / p


class TerminalBenchTools:
    """Shell execution tool for Terminal Bench agents running inside a container.

    Provides ``execute()`` for running arbitrary shell commands in the container.
    Commands run as root with the given ``workdir`` as the current directory.
    """

    def __init__(self, workdir: str = "/app") -> None:
        self._workdir = workdir

    async def execute(self, command: str, timeout: float = 300.0) -> str:
        """Execute a shell command inside the container and return combined stdout+stderr.

        Args:
            command: Shell command to run (passed to bash -c).
            timeout: Maximum seconds to wait (default 300s).

        Returns:
            Combined stdout and stderr output as a string.
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._workdir,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return f"[Command timed out after {timeout}s]"
            output = stdout.decode(errors="replace") if stdout else ""
            if proc.returncode != 0:
                output += f"\n[Exit code: {proc.returncode}]"
            return output
        except Exception as e:
            return f"[Error executing command: {e}]"
