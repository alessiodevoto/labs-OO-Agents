# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Disposable Doer agent — executes a single todo item.

Created fresh per ``do_it()`` call so no history accumulates between tasks.
Shares the parent TUI agent's shell, repo, and todo instances.
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

    from nemo_oo_agents import Agent, SkillManager, strategy
    from nemo_oo_agents.config import CodeActConfig
    from nemo_oo_agents.storage.markers import nosnapshot
    from nemo_oo_agents.strategies import CodeActStrategy
    from nemo_oo_agents.tools import TodoManager
    from nemo_oo_agents.tools.repo_tools import RepoTools
    from nemo_oo_agents.tools.shell_tools import ShellTools
    from nemo_oo_agents.tools.todo import Todo


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
    - self.shell — persistent shell + file ops: bash(), view(), edit(), write(), grep(), find(), ls()
    - self.repo — repo intelligence: filemap(), repo_map(), search_symbol()
    - self.todo — view and update the todo list (self.todo.status(), self.todo.done(), etc.)
    Plus any skills discovered via SkillManager (use doc(self) to see all).
    """

    shell: Annotated[ShellTools, nosnapshot]
    repo: Annotated[RepoTools, nosnapshot]
    todo: Annotated[TodoManager, nosnapshot]

    def __init__(
        self,
        *,
        shell: ShellTools,
        repo: RepoTools,
        todo: TodoManager,
        skills_dirs: list[Path] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.shell = shell
        self.repo = repo
        self.todo = todo

        # Install plugin skills (WTF, etc.) discovered via entry points
        dirs = skills_dirs if skills_dirs is not None else _discover_skills_dirs()
        if dirs:
            SkillManager.install(self, skills_dir=dirs)

    @strategy(CodeActStrategy(config=CodeActConfig(cell_timeout=1800.0)))
    async def execute(self, todo: "Todo") -> str:
        """Execute a single todo item and return a summary of what was done.

        Todo: [{todo.id}] {todo.title}
        Notes: {todo.notes}

        Instructions:
        1. Read the todo title and notes carefully.
        2. Execute the work described using self.shell (bash, view, edit, write, grep, find, ls).
        3. When done, update the todo with what you learned:
           self.todo.update("{todo.id}", notes="what you did and found")
        4. Mark it complete: self.todo.done("{todo.id}")
        5. Return a concise summary of what you did and the outcome.

        Focus only on this one item. Do NOT work on other todos.
        """
        ...
