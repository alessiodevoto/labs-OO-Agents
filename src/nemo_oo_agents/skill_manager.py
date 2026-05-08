# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SkillManager — discover and install Skills on an agent."""

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from nemo_oo_agents.skill import Skill, TextSkill

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent

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


class SkillManager:
    """Discover and install Skills on an agent.

    # Install skills from a directory — sets attrs directly on the agent
    self._skills = SkillManager.install(self, skills_dir=Path("skills"))
    self._skills = SkillManager.install(self, skills_dir=[Path("skills"), Path("extra")])

    # Discover skills without attaching to an agent (e.g. for CLI listing)
    skills = SkillManager.discover(Path("skills"))

    # Load a single skill directly:
    skill = TextSkill(path=Path("skills/git-workflow"))
    """

    def __init__(self, agent: "Agent", skills_dirs: list[Path]) -> None:
        self.agent = agent
        self.skills_dirs = skills_dirs

    @classmethod
    def install(
        cls,
        agent: "Agent",
        *,
        skills_dir: Path | list[Path] | str = Path("."),
    ) -> Self:
        """Scan *skills_dir* and attach each skill as an instance attribute on *agent*.

        Each subdirectory containing a SKILL.md becomes ``agent.<dir_name>``
        (hyphens replaced with underscores). Already-present attributes are skipped.

        Returns the SkillManager instance for further use.
        """
        if isinstance(skills_dir, str):
            dirs = [Path(skills_dir)]
        elif isinstance(skills_dir, Path):
            dirs = [skills_dir]
        else:
            dirs = list(skills_dir)

        manager = cls(agent, dirs)
        manager._scan()
        return manager

    def _scan(self) -> None:
        for skills_dir in self.skills_dirs:
            self._scan_dir(skills_dir)

    def _attach_skill(self, attr_name: str, skill: Skill) -> None:
        """Install a skill on the agent."""
        setattr(self.agent, attr_name, skill)
        skill.attach(self.agent)

    def _scan_dir(self, skills_dir: Path) -> None:
        if not skills_dir.is_dir():
            logger.debug("Skills directory %s does not exist", skills_dir)
            return
        for entry in skills_dir.iterdir():
            if entry.is_dir() and (entry / "SKILL.md").exists():
                self._install_text_skill(entry)
                continue
            if _is_python_skill_file(entry):
                self._install_python_skill(entry)

    def _install_text_skill(self, entry: Path) -> None:
        attr_name = entry.name.replace("-", "_").replace(" ", "_")
        if hasattr(self.agent, attr_name):
            logger.warning(
                f"Skill directory {entry.name} skipped: attribute {attr_name} already exists on {type(self.agent).__name__}",
            )
            return
        try:
            skill = TextSkill(path=entry)
            self._attach_skill(attr_name, skill)
        except Exception:
            logger.warning(
                f"Skill directory {entry.name} skipped: failed to load SKILL.md",
                exc_info=True,
            )

    def _install_python_skill(self, entry: Path) -> None:
        # Match the TextSkill convention: hyphens and spaces become
        # underscores so ``my-skill.py`` attaches as ``agent.my_skill``
        # (``agent.my-skill`` would be a syntax error).
        attr_name = entry.stem.replace("-", "_").replace(" ", "_")
        if hasattr(self.agent, attr_name):
            logger.warning(
                f"Skill file {entry.name} skipped: attribute {attr_name} already exists on {type(self.agent).__name__}",
            )
            return
        skill = _load_python_skill(entry)
        if skill is not None:
            self._attach_skill(attr_name, skill)

    @staticmethod
    def discover(paths: Path | list[Path]) -> dict[str, Skill]:
        """Recursively discover all skills in one or more directories.

        Finds both SKILL.md directories and top-level Python skill files
        (``*.py`` containing a ``Skill`` subclass). Python-skill keys use the
        filename stem; text-skill keys use the SKILL.md ``name`` field.

        Returns:
            Mapping of skill ID → Skill instance.
        """
        if isinstance(paths, Path):
            paths = [paths]

        skills: dict[str, Skill] = {}
        for base in paths:
            resolved = Path(base).resolve()
            for skill_md in resolved.rglob("SKILL.md"):
                skill_dir = skill_md.parent
                try:
                    skill = TextSkill(path=skill_dir)
                    skills[skill.id] = skill
                except Exception:
                    logger.warning(f"Skipped {skill_dir}: failed to load SKILL.md", exc_info=True)
            if resolved.is_dir():
                for py_file in resolved.iterdir():
                    if not _is_python_skill_file(py_file):
                        continue
                    skill = _load_python_skill(py_file)
                    if skill is not None:
                        skills[py_file.stem] = skill
        return skills


def _is_python_skill_file(entry: Path) -> bool:
    """True if *entry* is a candidate Python skill file.

    Skips:
      - directories (covered by SKILL.md discovery)
      - files whose name starts with '_' (private helpers, ``__init__.py``,
        and anything else the user explicitly marks as non-skill)
      - non ``.py`` suffix files
    """
    return entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_")
