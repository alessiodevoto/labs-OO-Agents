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

# ---------------------------------------------------------------------------
# LintReport
# ---------------------------------------------------------------------------


@dataclass
class LintReport:
    """Result of linting a library file.

    written: True if the file was written to disk.
    loaded:  True if the library was (re-)loaded on the agent.
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

    ## Always write a Skill subclass

    Your library's ``__init__.py`` should export a ``Skill`` subclass.
    This gives the agent full method discovery — ``doc(self.<lib_name>)``
    shows every public method, their signatures, and docstrings.

    When the library manager finds a ``Skill`` subclass (or a module-level
    ``skill = SomeSkill(...)`` instance), it attaches that as
    ``self.<lib_name>``. Without one, the module itself is wrapped
    via ``Skill(module)`` — you get the module docstring and attributes
    but not method-level discovery.

    Example ``__init__.py``::

        '''Grep-style search across multiple repos.'''
        from nemo_oo_agents.skill import Skill

        class MultiGrep(Skill):
            '''Grep across a configured list of repos.'''
            requires = ("nemo/shell",)  # declare dependencies

            def __init__(self):
                self._roots = [...]

            def search(self, pattern: str) -> list[str]:
                ...

    ## Lifecycle

        # 1. Scaffold the package
        await self.libs.create("stats", "Statistical utilities for numerical data.")

        # 2. Write code using self.shell
        await self.shell.write(f"{self.libs._path}/stats/stats.py", source)

        # 3. Hot-reload to pick up changes
        await self.libs.reload("stats")
        # → self.stats is now available for use

        # 4. Use it
        result = self.stats.percentile(my_data, 95)

        # 5. Edit + reload
        await self.shell.edit(f"{self.libs._path}/stats/stats.py", old, new)
        await self.libs.reload("stats")

        # 6. Test
        await self.shell.write(f"{self.libs._path}/stats/tests/test_stats.py", test_src)
        await self.libs.run_tests("stats")

    ## Skill features

    Skills can declare dependencies via the ``requires`` class attribute::

        class MySkill(Skill):
            requires = ("nemo/shell", "nemo/repo")

    Skills are registered in ``pyproject.toml`` entry points for auto-discovery::

        [project.entry-points."nemo_oo_agents.skills"]
        "nemo/myskill" = "my_package:MySkill"

    To make a skill a TUI slash command, add a ``SKILL.md`` with frontmatter::

        ---
        name: mycommand
        description: Do something useful
        argument-hint: <action>
        ---
        Body text shown to the agent when /mycommand is invoked.

    ## Discovery

        await self.libs.list()            # all library names
        # Use self.shell/self.repo for file viewing, grep, tree

    ## Rules
    - Always provide a meaningful description when calling create()
    - Library code is plain Python only — no `self`, no `...` bodies, no async
    - Always run run_tests() after creating or editing before claiming done
    - Use a library for logic worth naming and reusing; use inline code for one-offs
    - Always write a ``Skill`` subclass in ``__init__.py`` — this is how
      ``doc(self.<lib>)`` discovers your library's API
    - After editing library files with self.shell, call ``self.libs.reload(name)``
    """

    def __init__(self, agent: Any, path: Path) -> None:
        self._agent = agent
        self._path = path
        self._path.mkdir(parents=True, exist_ok=True)
        self._shell = agent.shell
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

        # Scaffold a Skill subclass so doc(self.<lib>) shows methods.
        class_name = "".join(w.capitalize() for w in lib_name.split("_"))
        init_content = (
            f'"""{description}"""\n'
            f"from nemo_oo_agents.skill import Skill\n\n\n"
            f"class {class_name}(Skill):\n"
            f'    """{first_line}"""\n'
        )
        (lib_dir / "__init__.py").write_text(init_content)

        return f"Created library '{lib_name}' at {lib_dir}"

    async def reload(self, lib_name: str) -> str:
        """Hot-reload a library after editing its files via self.shell.

        Call this after using self.shell.write() or self.shell.edit() on library files.
        Re-imports the package module and re-attaches the updated Skill to the agent.

        Returns:
            Confirmation message or error description.
        """
        lib_dir = self._path / lib_name
        if not lib_dir.is_dir():
            return f"Library '{lib_name}' not found at {self._path}"
        try:
            self._libmgr._reload(lib_name)
            return f"Reloaded library '{lib_name}' — self.{lib_name} is updated."
        except Exception as e:
            return f"Reload failed for '{lib_name}': {e}"

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
        result = await self._shell.run(
            f"PYTHONPATH={shlex.quote(str(self._path))}:$PYTHONPATH "
            f"{shlex.quote(_sys.executable)} -m pytest {shlex.quote(str(tests_dir))} -v",
            timeout=60,
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

        # Libraries are standard Python packages — no import restrictions.
        # Only basic syntax/security validation applies (E001 errors).
        context = ValidationContext(
            code=source,
            agent_class=type(self._agent),
            available_names=set(),
            importable_modules=importable,
            restricted_imports=frozenset(),
            blocked_modules=frozenset(),
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
