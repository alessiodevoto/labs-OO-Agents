# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LibraryWriting — create and edit persistent code libraries for agents."""

import ast
import inspect
import re
import sys
import textwrap
import types
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nemo_oo_agents.library_manager import LibraryManager
from nemo_oo_agents.skill import Skill
from nemo_oo_agents.tools.bash_tool import BashTool, FileTool

# ---------------------------------------------------------------------------
# LintReport
# ---------------------------------------------------------------------------


@dataclass
class LintReport:
    """Result of linting a library file.

    written: True if the file was written to disk.
    loaded:  True if the library was (re-)loaded into exec_globals.
    errors:  Hard errors — E001 (forbidden builtins), E003 (star imports).
    warnings: Soft issues — E002 (import not in agent's allowed set).
    """

    written: bool = False
    loaded: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if not self.written:
            status = "ERROR — file not written"
        elif not self.loaded:
            status = "WARNING — written but not loaded"
        else:
            status = "OK — written and loaded"

        issues = [f"  \u2717 {e}" for e in self.errors] + [f"  \u26a0 {w}" for w in self.warnings]
        if not issues:
            return status
        return "\n".join([status, *issues])


# ---------------------------------------------------------------------------
# LibraryWriting
# ---------------------------------------------------------------------------


class LibraryWriting(Skill):
    """Write persistent Python libraries that survive across sessions.

    Libraries are standard Python packages stored at a caller-specified path.
    Any valid package layout is accepted — no specific file name is required.

    ## Lifecycle

        # 1. Scaffold the package
        await self.libs.create("stats", "Statistical utilities for numerical data.")

        # 2. Write code (any .py file name)
        await self.libs.write_file("stats", "stats.py", source)
        # → lints, writes, hot-reloads; stats is now available for use, no need to import it again

        # 3. Use it directly
        result = stats.percentile(my_data, 95)

        # 4. Edit
        await self.libs.edit_file("stats", "stats.py", old_block, new_block)

        # 5. Test
        await self.libs.write_file("stats", "tests/test_stats.py", test_source)
        await self.libs.run_tests("stats")

    ## Discovery

        await self.libs.list()       # all library names
        await self.libs.repo_tree()  # directory tree
        await self.libs.grep(pat)    # search across all library files

    ## Rules
    - Always provide a meaningful description when calling create()
    - Library code is plain Python only — no `self`, no `...` bodies, no async
    - Always run run_tests() after creating or editing before claiming done
    - Use a library for logic worth naming and reusing; use inline code for one-offs
    """

    def __init__(self, agent: Any, path: Path) -> None:
        self._agent = agent
        self._path = path
        self._path.mkdir(parents=True, exist_ok=True)
        self._bash = BashTool(working_dir=self._path)
        self._files = FileTool(self._bash)
        libs_str = str(self._path)
        if libs_str not in sys.path:
            sys.path.insert(0, libs_str)
        self._libmgr = LibraryManager.install(self._agent, libs_dir=self._path)
        super().__init__()

    async def list(self) -> list[str]:
        """Return sorted names of all libraries (directories with a pyproject.toml)."""
        return self._libmgr.discover(self._path)

    async def create(self, lib_name: str, description: str) -> str:
        """Scaffold a new library: writes pyproject.toml + __init__.py.

        Nothing is loaded yet. Call write_file() to add code in any layout you like.

        Args:
            lib_name: Module name for the library (e.g. "stats").
            description: Human-readable description stored as the module docstring.

        Returns:
            Confirmation string with the library path.
        """
        lib_dir = self._path / lib_name
        lib_dir.mkdir(parents=True, exist_ok=True)

        first_line = description.strip().split("\n")[0]
        pyproject = textwrap.dedent(f"""\
            [project]
            name = "{lib_name}"
            version = "0.1.0"
            description = "{first_line}"
            dependencies = []
        """)
        (lib_dir / "pyproject.toml").write_text(pyproject)
        (lib_dir / "__init__.py").write_text(f'"""{description}"""\n')

        return f"Created library '{lib_name}' at {lib_dir}"

    async def write_file(self, lib_name: str, path: str, content: str) -> str:
        """Write a file within the library directory.

        For any .py file (except __init__.py): lints the content, writes if no hard errors,
        and hot-reloads the library if the lint report is clean. This includes test files
        (e.g. tests/test_foo.py).

        For __init__.py: writes directly (star re-exports are allowed here).

        For pyproject.toml: writes and checks declared dependencies.

        For non-.py, non-pyproject paths (e.g. README.md, data.json): writes without linting.

        Args:
            lib_name: Library name.
            path: Relative path within the library (e.g. "stats.py", "tests/test_lib.py").
            content: File content to write.

        Returns:
            LintReport string for .py/pyproject.toml, plain confirmation otherwise.
        """
        dest = self._path / lib_name / path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if path.endswith(".py"):
            declared_deps = self._get_declared_deps(lib_name)
            report = self._lint_source(content, declared_deps)
            if report.errors:
                return str(report)
            dest.write_text(content)
            report.written = True
            if not report.warnings:
                self._libmgr._reload(lib_name)
                report.loaded = True
            return str(report)

        if path == "pyproject.toml":
            dest.write_text(content)
            deps = self._parse_pyproject_deps(content)
            report = self._lint_pyproject(deps)
            report.written = True
            return str(report)

        dest.write_text(content)
        return f"Written {len(content)} bytes to {lib_name}/{path}"

    async def edit_file(
        self,
        lib_name: str,
        path: str,
        search_block: str,
        replace_block: str,
    ) -> str:
        """Edit a file in the library using search/replace.

        For any .py file (except __init__.py): lints the result and hot-reloads if clean.

        Args:
            lib_name: Library name.
            path: Relative path within the library.
            search_block: Exact string to find (must be unique in the file).
            replace_block: Replacement string.

        Returns:
            LintReport string for .py files, plain confirmation otherwise.
        """
        result = await self._files.edit_file(
            str(self._path / lib_name / path),
            search_block,
            replace_block,
        )

        if path.endswith(".py"):
            source = (self._path / lib_name / path).read_text()
            declared_deps = self._get_declared_deps(lib_name)
            report = self._lint_source(source, declared_deps)
            report.written = True
            if not report.errors and not report.warnings:
                self._libmgr._reload(lib_name)
                report.loaded = True
            return str(report)

        if path == "pyproject.toml":
            content = (self._path / lib_name / path).read_text()
            deps = self._parse_pyproject_deps(content)
            report = self._lint_pyproject(deps)
            report.written = True
            return str(report)

        return str(result)

    async def view_file(self, lib_name: str, path: str) -> str:
        """Read and return the contents of a file in the library.

        Args:
            lib_name: Library name.
            path: Relative path within the library.

        Returns:
            File contents as a string.
        """
        result = await self._files.read(str(self._path / lib_name / path))
        return result.stdout

    async def grep(self, pattern: str, directory: str = ".") -> str:
        """Search for a pattern across library files.

        Args:
            pattern: Regex pattern to search for.
            directory: Subdirectory to search in (relative to libs root, default ".").

        Returns:
            Grep output as a string.
        """
        import shlex

        result = await self._bash.run(f"grep -rn {shlex.quote(pattern)} {shlex.quote(directory)}")
        return str(result)

    async def repo_tree(self, directory: str = ".") -> str:
        """Show the directory tree of the libraries root (or a subdirectory).

        Args:
            directory: Subdirectory to show (relative to libs root, default ".").

        Returns:
            Tree output as a string.
        """
        import shlex

        result = await self._bash.run(f"find {shlex.quote(directory)} -not -path '*/.*' | sort")
        return str(result)

    async def run_tests(self, lib_name: str) -> str:
        """Run pytest on the library's tests/ directory.

        Args:
            lib_name: Library name.

        Returns:
            Pytest output as a string.
        """
        import shlex
        import sys as _sys

        tests_dir = self._path / lib_name / "tests"
        result = await self._bash.run(
            f"PYTHONPATH={shlex.quote(str(self._path))}:$PYTHONPATH "
            f"{shlex.quote(_sys.executable)} -m pytest {shlex.quote(str(tests_dir))} -v",
            timeout=60.0,
        )
        return str(result)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lint_source(self, source: str, declared_deps: set[str]) -> LintReport:
        """Run SecurityValidator on library source code.

        Only SecurityValidator applies — library source is plain Python,
        not a REPL cell, so REPL/CodeAct policies don't apply.
        """
        from nemo_oo_agents.runtime.code_validator import SecurityValidator, ValidationContext

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return LintReport(errors=[f"SyntaxError: {e}"])

        importable = self._importable_modules() | declared_deps

        context = ValidationContext(
            code=source,
            agent_class=type(self._agent),
            available_names=set(),
            importable_modules=importable,
        )

        issues = SecurityValidator().validate(tree, context)

        errors: list[str] = []
        warnings: list[str] = []
        for issue in issues:
            msg = f"{issue.code}: {issue.message} (line {issue.line})"
            if issue.code == "E001":
                errors.append(msg)
            else:
                warnings.append(msg)

        return LintReport(errors=errors, warnings=warnings)

    def _lint_pyproject(self, deps: Sequence[str]) -> LintReport:
        """Check declared dependencies against agent's importable modules."""
        importable = self._importable_modules()
        warnings = [
            f"E002: '{dep}' is declared but not in agent's importable modules"
            for dep in deps
            if dep not in importable
        ]
        return LintReport(warnings=warnings)

    def _importable_modules(self) -> set[str]:
        """Get the set of module names visible in the agent's module namespace."""
        agent_module = inspect.getmodule(type(self._agent))
        ns = agent_module.__dict__ if agent_module else {}
        return {obj.__name__ for obj in ns.values() if isinstance(obj, types.ModuleType)}

    def _get_declared_deps(self, lib_name: str) -> set[str]:
        """Parse declared dependencies from the library's pyproject.toml."""
        pyproject_path = self._path / lib_name / "pyproject.toml"
        if not pyproject_path.exists():
            return set()
        return set(self._parse_pyproject_deps(pyproject_path.read_text()))

    def _parse_pyproject_deps(self, content: str) -> Sequence[str]:
        """Extract dependency names from pyproject.toml content."""
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
        if not m:
            return []
        deps: list[str] = []
        for line in m.group(1).splitlines():
            m2 = re.search(r'["\']([^"\']+)["\']', line)
            if m2:
                dep_spec = m2.group(1)
                dep_name = re.split(r"[>=<!,;]", dep_spec)[0].strip()
                if dep_name:
                    deps.append(dep_name)
        return deps
