# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Skill utilities — extract skills from modules, load Python skill files."""

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from nemo_oo_agents.skill import Skill, TextSkill

logger = logging.getLogger(__name__)


def skill_from_module(module: Any, module_name: str, source: str = "") -> Skill | None:
    """Extract a ``Skill`` instance from an already-imported module.

    Shared by :func:`_load_python_skill` (for ``*.py`` files under a
    skills dir) and by ``LibraryManager`` (for agent-written libraries
    whose top-level module may export a ``Skill``).

    Resolution order:

    1. Module-level ``skill`` attribute that is a ``Skill`` instance.
       Use this to construct with non-trivial args (``skill = MySkill(a=1)``).
    2. First ``Skill`` subclass *defined in this module*
       (``cls.__module__`` equals ``module_name``). Locally-defined beats
       re-exported, so ``class MyLocal(Skill)`` wins over
       ``from other import Existing``.
    3. First re-exported ``Skill`` subclass in the module namespace.

    ``source`` is a human-readable tag used in log messages (e.g. the
    file path or library name).

    Returns ``None`` when none found, or when the class can't be
    instantiated without args. All failures log at WARNING.
    """
    # Module-level ``skill`` instance wins
    explicit = vars(module).get("skill")
    if isinstance(explicit, Skill):
        return explicit

    # Otherwise scan for Skill subclasses. Framework bases filtered by
    # identity so ``from nemo_oo_agents.skill import Skill`` in a helper
    # file doesn't trigger a spurious install.
    local: list[type] = []
    reexported: list[type] = []
    for _name, obj in vars(module).items():
        if not inspect.isclass(obj):
            continue
        if not issubclass(obj, Skill):
            continue
        if obj is Skill or obj is TextSkill:
            continue
        if obj.__module__ == module_name:
            local.append(obj)
        else:
            reexported.append(obj)
    candidates = local + reexported
    if not candidates:
        return None
    if len(candidates) > 1:
        picked = candidates[0]
        extras = ", ".join(c.__name__ for c in candidates[1:])
        logger.warning(
            "%s defines multiple Skill subclasses; using %s, ignoring: %s",
            source or module_name,
            picked.__name__,
            extras,
        )
    cls = candidates[0]
    try:
        return cls()
    except TypeError:
        logger.warning(
            "%s skipped: class %s needs constructor args "
            "(Python skills must have a zero-arg __init__)",
            source or module_name,
            cls.__name__,
            exc_info=True,
        )
        return None
    except Exception:
        logger.warning(
            "%s skipped: %s.__init__ raised",
            source or module_name,
            cls.__name__,
            exc_info=True,
        )
        return None


def _load_python_skill(path: Path) -> Skill | None:
    """Import *path* and extract a ``Skill`` via :func:`skill_from_module`.

    Failures (can't-import, no skill found, constructor needs args) log at
    WARNING and return ``None`` so a single broken file never aborts
    discovery.
    """
    # Include a hash of the absolute path in the module name so two files
    # with the same stem in different skills_dirs don't collide in
    # sys.modules.
    module_name = f"_nemo_oo_skill_{path.stem}_{abs(hash(str(path.resolve()))) & 0xFFFFFFFF:08x}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning("Skill file %s skipped: could not build import spec", path)
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
    except Exception:
        logger.warning("Skill file %s skipped: import failed", path, exc_info=True)
        return None

    try:
        return skill_from_module(module, module_name, source=f"Skill file {path}")
    finally:
        # The Skill instance keeps any class objects it needs alive via
        # its own references. Drop our sys.modules entry so discovery
        # doesn't pollute the global namespace.
        sys.modules.pop(module_name, None)





def _is_python_skill_file(entry: Path) -> bool:
    """True if *entry* is a candidate Python skill file.

    Skips:
      - directories (covered by SKILL.md discovery)
      - files whose name starts with '_' (private helpers, ``__init__.py``,
        and anything else the user explicitly marks as non-skill)
      - non ``.py`` suffix files
    """
    return entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_")
