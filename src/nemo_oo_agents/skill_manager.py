"""SkillManager — discover and install Skills on an agent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nemo_oo_agents.skill import Skill, TextSkill

if TYPE_CHECKING:
    from nemo_oo_agents.agent import Agent

logger = logging.getLogger(__name__)


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

    def __init__(self, agent: Agent, skills_dirs: list[Path]) -> None:
        self.agent = agent
        self.skills_dirs = skills_dirs

    @classmethod
    def install(
        cls,
        agent: Agent,
        *,
        skills_dir: Path | list[Path] | str = Path("."),
    ) -> SkillManager:
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
            if not (entry.is_dir() and (entry / "SKILL.md").exists()):
                continue
            attr_name = entry.name.replace("-", "_").replace(" ", "_")
            if hasattr(self.agent, attr_name):
                logger.warning(
                    f"Skill directory {entry.name} skipped: attribute {attr_name} already exists on {type(self.agent).__name__}",
                )
                continue
            try:
                setattr(self.agent, attr_name, TextSkill(path=entry))
            except Exception:
                logger.warning(
                    f"Skill directory {entry.name} skipped: failed to load SKILL.md",
                    exc_info=True,
                )

    @staticmethod
    def discover(paths: Path | list[Path]) -> dict[str, Skill]:
        """Recursively discover all skills in one or more directories.

        Returns:
            Mapping of skill ID → Skill instance.
        """
        if isinstance(paths, Path):
            paths = [paths]

        skills: dict[str, Skill] = {}
        for base in paths:
            for skill_md in Path(base).resolve().rglob("SKILL.md"):
                skill_dir = skill_md.parent
                try:
                    skill = TextSkill(path=skill_dir)
                    skills[skill.id] = skill  # type: ignore[index]
                except Exception:
                    logger.warning(f"Skipped {skill_dir}: failed to load SKILL.md", exc_info=True)
        return skills
