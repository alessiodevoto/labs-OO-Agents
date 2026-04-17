# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SkillManager — discover and install Skills on an agent."""

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Self

from nemo_oo_agents.skill import Skill, TextSkill

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent

logger = logging.getLogger(__name__)


def _load_python_skill(path: Path) -> Skill | None:
    """Import *path* and instantiate the first ``Skill`` subclass defined there.

    Returns None if the module can't be imported, defines no own ``Skill``
    subclass, or the class requires constructor args. All failures are logged
    at WARNING so a single broken file doesn't abort skill discovery.
    """
    module_name = f"_nemo_oo_skill_{path.stem}"
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

    # Module-level ``skill`` instance wins — lets the file construct with
    # custom args (``skill = MySkill(foo=bar)``).
    explicit = vars(module).get("skill")
    if isinstance(explicit, Skill):
        return explicit

    # Otherwise scan for Skill subclasses in the module namespace. Both local
    # definitions and simple re-exports count — ``from pkg.foo import MySkill``
    # in a skills-dir file is a valid way to expose a class for discovery.
    # The framework bases (``Skill``, ``TextSkill``) are filtered by identity
    # so ``from nemo_oo_agents.skill import Skill`` in a helper file doesn't
    # trigger a spurious install.
    candidates = [
        obj
        for _name, obj in vars(module).items()
        if inspect.isclass(obj)
        and issubclass(obj, Skill)
        and obj is not Skill
        and obj is not TextSkill
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        picked = candidates[0]
        extras = ", ".join(c.__name__ for c in candidates[1:])
        logger.warning(
            "Skill file %s defines multiple Skill subclasses; using %s, ignoring: %s",
            path,
            picked.__name__,
            extras,
        )
    cls = candidates[0]
    try:
        return cls()
    except TypeError:
        logger.warning(
            "Skill file %s skipped: class %s needs constructor args "
            "(Python skills must have a zero-arg __init__)",
            path,
            cls.__name__,
            exc_info=True,
        )
        return None
    except Exception:
        logger.warning(
            "Skill file %s skipped: %s.__init__ raised", path, cls.__name__, exc_info=True
        )
        return None


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
            setattr(self.agent, attr_name, TextSkill(path=entry))
        except Exception:
            logger.warning(
                f"Skill directory {entry.name} skipped: failed to load SKILL.md",
                exc_info=True,
            )

    def _install_python_skill(self, entry: Path) -> None:
        attr_name = entry.stem
        if hasattr(self.agent, attr_name):
            logger.warning(
                f"Skill file {entry.name} skipped: attribute {attr_name} already exists on {type(self.agent).__name__}",
            )
            return
        skill = _load_python_skill(entry)
        if skill is not None:
            setattr(self.agent, attr_name, skill)

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
