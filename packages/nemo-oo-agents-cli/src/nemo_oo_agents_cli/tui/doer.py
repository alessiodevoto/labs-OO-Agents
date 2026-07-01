# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Disposable Doer agent — executes a single todo item.

Created fresh per ``make_doer()`` call so no history accumulates between tasks.
Each doer gets its own ShellTools session (isolated cwd/state).
"""

# Module-level imports visible to the Doer's CodeAct REPL
import json  # noqa: F401
import re  # noqa: F401
from pathlib import Path  # noqa: F401

# Optional data science libraries (match TUI agent, minus plotting)
try:
    import numpy as np  # noqa: F401  # type: ignore[import-untyped]
except ImportError:
    pass

try:
    import pandas as pd  # noqa: F401  # type: ignore[import-untyped]
except ImportError:
    pass

from nemo_oo_agents import hidden

with hidden:
    from typing import Annotated

    from nemo_oo_agents import Agent, strategy
    from nemo_oo_agents.config import CodeActConfig
    from nemo_oo_agents.storage.markers import nosnapshot
    from nemo_oo_agents.strategies import CodeActStrategy
    from nemo_oo_agents.tools import TodoManager
    from nemo_oo_agents.tools.shell_tools import ShellTools
    from nemo_oo_agents.tools.todo import Todo
    from nemo_oo_agents_cli.tools.repo_tools import RepoTools


def _discover_skills_dirs() -> list[Path]:
    """Discover skill directories from entry points and standard locations."""
    dirs: list[Path] = [
        Path(".claude/skills"),
        Path(".claude/commands"),
    ]
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="nemo_oo_tui.skills_dirs"):
            try:
                dirs.append(Path(str(ep.load()())))
            except Exception:
                pass
    except Exception:
        pass
    return [d for d in dirs if d.exists()]


class DoerAgent(Agent):
    """One-shot executor for a single todo item.

    You have these tools:
    - self.shell — ShellTools: run(cmd, stdin=), read(path, lines=), write_file(path, content),
      replace(match/region/path, ...), rg(pat, path), cat(), find(), run_pipe(), lines(path, s, e)
    - self.repo — repo intelligence: filemap(), repo_map(), search_symbol()
    - self.todo — view and update the todo list (self.todo.status(), self.todo.done(), etc.)
    Plus any skills discovered via SkillRegistry (use doc(self) to see all).
    """

    shell: Annotated[ShellTools, nosnapshot]
    repo: Annotated[RepoTools, nosnapshot]
    todo: Annotated[TodoManager, nosnapshot]

    def __init__(
        self,
        *,
        cwd: Path,
        repo: RepoTools,
        todo: TodoManager,
        skills_dirs: list[Path] | None = None,
        shell_cls: type | None = None,
        memory_config=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        # Use the shell variant the parent is currently running (so /swap-shell
        # propagates to doers); default to the module's ShellTools.
        self.shell = (shell_cls or ShellTools)(cwd=cwd)
        self.repo = repo
        self.todo = todo
        # Render the shell's API surface from the *live* shell type each turn.
        # set_static with expr= re-evaluates the expression per turn (like set_dynamic)
        # but places the block in the cacheable prefix for better prompt caching.
        self.context.set_static("self.shell", expr="doc(type(self.shell))")

        # Skill registry: register passed-in skills + discover plugins
        from nemo_oo_agents.skill_registry import SkillRegistry

        self.skills = SkillRegistry(self)
        self.skills.register("nemo.shell", self.shell)
        self.skills.register("nemo.repo", self.repo)
        self.skills.register("nemo.todo", self.todo)

        # Install plugin skills (WTF, etc.) discovered via entry points
        dirs = skills_dirs if skills_dirs is not None else _discover_skills_dirs()
        if dirs:
            self.skills.discover_skills_dirs(dirs)

        # Activate all built-ins except memory; TUI wires memory explicitly below
        # so the configured scope/path is shared with the parent agent.
        builtin_skills = [
            n for n in self.skills.discovered() if n.startswith("nemo.") and n != "nemo.memory"
        ]
        self.skills.activate(builtin_skills)

        if memory_config is not None:
            from nemo_oo_agents.memory.memory_skill import MemorySkill

            self.skills.register("nemo.memory", MemorySkill(memory_config))
            self.skills.activate(["nemo.memory"])

    @strategy(CodeActStrategy(config=CodeActConfig(cell_timeout=1800.0)))
    async def execute(self, todo: "Todo") -> str:
        """Execute a single todo item and return a summary of what was done.

        Todo: [{todo.id}] {todo.title}
        Notes: {todo.notes}

        Instructions:
        1. Read the todo title and notes carefully.
        2. Execute the work described using self.shell (run, read, write_file, replace, rg, cat, find).
        3. When done, update the todo with what you learned:
           self.todo.update("{todo.id}", notes="what you did and found")
        4. Mark it complete: self.todo.done("{todo.id}")
        5. Return a concise summary of what you did and the outcome.

        Focus only on this one item. Do NOT work on other todos.
        """
        ...
