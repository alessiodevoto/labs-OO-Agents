# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LibraryManager — scan, load, and hot-reload persistent agent libraries."""

import inspect
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nemo_oo_agents.library_skill import LibrarySkill

if TYPE_CHECKING:
    from nemo_oo_agents.skill import Skill

logger = logging.getLogger(__name__)


class LibraryManager:
    """Scans a libs directory and attaches each Python package as an agent attribute.

    Each subdirectory containing a pyproject.toml is imported and set as
    ``agent.<lib_name>``. Supports hot-reload after edits via reload().

        mgr = LibraryManager.install(agent, libs_dir=Path("libs"))
        # agent.stats, agent.utils, ... are now set

        mgr.reload()              # reload every installed library

        LibraryManager.discover(path)  # list library names without loading
    """

    def __init__(self, agent: Any, libs_path: Path) -> None:
        self._agent = agent
        self._libs_path = libs_path
        self._installed: list[str] = []

    @classmethod
    def install(cls, agent: Any, *, libs_dir: Path) -> "LibraryManager":
        """Scan libs_dir and attach all libraries to *agent*. Returns the manager."""
        manager = cls(agent, libs_dir)
        manager._scan()
        return manager

    def _scan(self) -> None:
        if not self._libs_path.is_dir():
            return
        for lib_dir in sorted(self._libs_path.iterdir()):
            if not (lib_dir.is_dir() and (lib_dir / "pyproject.toml").exists()):
                continue
            lib_name = lib_dir.name
            if hasattr(self._agent, lib_name):
                continue
            try:
                self._attach(lib_dir, lib_name)
                self._installed.append(lib_name)
            except Exception:
                logger.warning("Library %s skipped", lib_name, exc_info=True)

    def _reload(self, lib_name: str) -> "Skill":
        """Re-import lib_name from disk and re-inject the module into the agent namespace.

        Returns whatever ``Skill`` ended up attached — a
        :class:`~nemo_oo_agents.library_skill.LibrarySkill` wrapper by
        default, or a user-defined ``Skill`` subclass if the library
        exports one (see :func:`_attach`).
        """
        lib_dir = self._libs_path / lib_name
        return self._attach(lib_dir, lib_name)

    def _attach(self, lib_dir: Path, lib_name: str) -> "Skill":
        """Import the library, decide what to attach, set it on the agent.

        The library module is always made bare-accessible on the agent's
        module (so ``stats.percentile(...)`` works). For ``agent.<lib_name>``:

        - If the library's top-level module exports a ``Skill`` (via a
          module-level ``skill = ...`` instance or a ``Skill`` subclass),
          attach that — lets agent-written libraries surface as first-class
          skills with their full API visible in ``doc(agent.<lib_name>)``.
        - Otherwise fall back to the :class:`LibrarySkill` doc wrapper.

        Either way the bare module is still in the exec namespace — skill
        attachment is additive, not a replacement.
        """
        # Always attach the raw module so bare-name access keeps working.
        wrapper = LibrarySkill(path=lib_dir)
        agent_module = inspect.getmodule(type(self._agent))
        if agent_module is not None:
            setattr(agent_module, lib_name, sys.modules[lib_name])
            logger.info("Library %s set on agent module", lib_name)

        # Prefer a user-defined Skill export if the library has one.
        attached: Skill = wrapper
        from nemo_oo_agents.skill_manager import skill_from_module

        module = sys.modules.get(lib_name)
        if module is not None:
            user_skill = skill_from_module(module, lib_name, source=f"Library {lib_name!r}")
            if user_skill is not None:
                attached = user_skill
                logger.info(
                    "Library %s exports %s — attached as agent.%s",
                    lib_name,
                    type(user_skill).__name__,
                    lib_name,
                )

        setattr(self._agent, lib_name, attached)
        return attached

    def reload(self) -> None:
        """Reload all installed libraries from disk."""
        for lib_name in self._installed:
            try:
                self._reload(lib_name)
            except Exception:
                logger.warning("Reload failed for %s", lib_name, exc_info=True)

    @staticmethod
    def discover(path: Path) -> list[str]:
        """Return sorted library names (directories with a pyproject.toml) under *path*."""
        return sorted(p.parent.name for p in path.glob("*/pyproject.toml") if p.parent.is_dir())
