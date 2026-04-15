# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LibraryManager — scan, load, and hot-reload persistent agent libraries."""

from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from nemo_oo_agents.library_skill import LibrarySkill

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
    def install(cls, agent: Any, *, libs_dir: Path) -> LibraryManager:
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
                lib = LibrarySkill(path=lib_dir)
                agent_module = inspect.getmodule(type(self._agent))
                if agent_module is not None:
                    setattr(agent_module, lib_name, sys.modules[lib_name])
                    logger.info("Library %s set on agent", lib_name)
                setattr(self._agent, lib_name, lib)
                self._installed.append(lib_name)
            except Exception:
                logger.warning("Library %s skipped", lib_name, exc_info=True)

    def _reload(self, lib_name: str) -> LibrarySkill:
        """Re-import lib_name from disk and re-inject the module into the agent namespace."""
        lib_dir = self._libs_path / lib_name
        lib = LibrarySkill(path=lib_dir)
        agent_module = inspect.getmodule(type(self._agent))
        if agent_module is not None:
            setattr(agent_module, lib_name, sys.modules[lib_name])
        setattr(self._agent, lib_name, lib)
        return lib

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
