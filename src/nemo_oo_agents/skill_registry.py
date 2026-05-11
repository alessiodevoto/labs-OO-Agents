# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SkillRegistry — discover, load, and activate skills on an agent."""

import fnmatch
import logging
from importlib.metadata import entry_points
from typing import Any

from nemo_oo_agents.skill import Skill

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "nemo_oo_agents.skills"

_RESERVED_ATTRS = frozenset(
    {
        "runtime",
        "event_manager",
        "context_manager",
        "_storage",
        "_llm",
        "_agent_id",
        "render_config",
        "event_query",
    }
)


class SkillRegistry(Skill):
    """Manages skill discovery, loading, and activation.

    Three-stage lifecycle:
    1. **Discover** — find all available skills (entry points, dirs, libs).
    2. **Load** — instantiate + attach a filtered subset to the agent.
    3. **Activate** — make loaded skills visible to the LLM via doc(self).

    Usage in agent constructor::

        self.skills.load(['stdskill/*'])
        self.skills.activate(['stdskill/shell', 'stdskill/repo'])
    """

    __agentdoc_skip__ = True
    __nosnapshot__ = True

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._discovered: dict[str, _SkillEntry] = {}
        self._loaded: set[str] = set()
        self._activated: set[str] = set()
        self._attr_map: dict[str, str] = {}  # registry name → actual attr name on agent
        self._discover()
        super().__init__()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Scan entry points for registered skills."""
        try:
            eps = entry_points(group=ENTRY_POINT_GROUP)
        except Exception:
            eps = []
        for ep in eps:
            self._discovered[ep.name] = _SkillEntry(
                name=ep.name,
                entry_point=ep,
                category=ep.name.split("/")[0] if "/" in ep.name else "",
            )

    def discovered(self) -> list[str]:
        """All discovered skill names (category/name format)."""
        return sorted(self._discovered.keys())

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def loaded(self) -> list[str]:
        """Currently loaded (attached) skill names."""
        return sorted(self._loaded)

    def load(self, patterns: list[str]) -> None:
        """Load skills matching patterns from the discovered set.

        Patterns support fnmatch globs: 'stdskill/*', '*', 'stdskill/shell'.
        Skills with empty constructors are auto-instantiated. Skills requiring
        args must be constructed manually and registered via register().
        """
        matched = self._match(patterns, set(self._discovered.keys()))
        for name in matched:
            if name in self._loaded:
                continue
            entry = self._discovered[name]
            try:
                skill_cls = entry.entry_point.load()
                if isinstance(skill_cls, type) and issubclass(skill_cls, Skill):
                    skill = skill_cls()
                elif isinstance(skill_cls, Skill):
                    skill = skill_cls
                else:
                    logger.warning("Entry point %s did not resolve to a Skill", name)
                    continue
                attr_name = name.split("/")[-1] if "/" in name else name
                attr_name = attr_name.replace("-", "_")
                if attr_name.startswith("_") or attr_name in _RESERVED_ATTRS:
                    logger.warning("Refusing to load skill with reserved name %s", attr_name)
                    continue
                setattr(self._agent, attr_name, skill)
                skill.attach(self._agent)
                self._loaded.add(name)
                self._attr_map[name] = attr_name
                logger.info("Loaded skill %s as self.%s", name, attr_name)
            except Exception:
                logger.warning("Failed to load skill %s", name, exc_info=True)

    def register(self, name: str, skill: Skill, *, attr: str | None = None) -> None:
        """Register a manually-constructed skill as loaded.

        Calls attach(agent) automatically if not already attached.
        Auto-detects the attribute name on the agent by identity scan.

        Use for skills that require constructor args::

            self.shell = ShellTools(cwd=config.working_dir)
            self.skills.register('stdskill/shell', self.shell)
        """
        if getattr(skill, "_agent", None) is None:
            skill.attach(self._agent)
        # Auto-detect actual attribute name on the agent
        if attr is None:
            attr = next((k for k, v in self._agent.__dict__.items() if v is skill), None)
        if attr is None:
            attr = self._attr_name(name)
        self._loaded.add(name)
        self._attr_map[name] = attr
        if name not in self._discovered:
            category = name.split("/")[0] if "/" in name else ""
            self._discovered[name] = _SkillEntry(name=name, entry_point=None, category=category)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activated(self) -> list[str]:
        """Currently activated (LLM-visible) skill names."""
        return sorted(self._activated)

    def activate(self, patterns: list[str]) -> None:
        """Make loaded skills matching patterns visible to the LLM.

        Also loads matching skills if not already loaded.
        Resolves dependencies transitively: if skill A requires B, activating A
        will auto-load B (but not activate it — deps stay hidden unless explicitly activated).
        Patterns support fnmatch globs.
        """
        # Auto-load if not already loaded
        not_loaded = self._match(patterns, set(self._discovered.keys()) - self._loaded)
        if not_loaded:
            self.load(list(not_loaded))

        matched = self._match(patterns, self._loaded)
        # Resolve transitive dependencies
        for name in list(matched):
            self._resolve_deps(name)

        for name in matched:
            self._activated.add(name)
            self._unhide_skill(name)

    def _resolve_deps(self, name: str, _visited: set[str] | None = None) -> None:
        """Load dependencies declared by the skill (transitive, with cycle detection)."""
        if _visited is None:
            _visited = set()
        if name in _visited:
            return
        _visited.add(name)

        attr = self._attr_map.get(name) or self._attr_name(name)
        skill = getattr(self._agent, attr, None)
        if skill is None:
            return
        requires = getattr(skill, "requires", ())
        for dep in requires:
            if dep not in self._loaded:
                if dep in self._discovered:
                    self.load([dep])
                else:
                    logger.warning("Skill %s requires %s but it is not discovered", name, dep)
            # Recurse into loaded dep's own requirements
            if dep in self._loaded:
                self._resolve_deps(dep, _visited)

    def deactivate(self, patterns: list[str]) -> None:
        """Hide activated skills from the LLM (still loaded)."""
        matched = self._match(patterns, self._activated)
        for name in matched:
            self._activated.discard(name)
            self._hide_skill(name)

    # ------------------------------------------------------------------
    # Visibility wiring
    # ------------------------------------------------------------------

    def _attr_name(self, name: str) -> str:
        """Convert category/skill_name to Python attribute name."""
        attr = name.split("/")[-1] if "/" in name else name
        return attr.replace("-", "_")

    def _unhide_skill(self, name: str) -> None:
        """Mark a skill attribute as visible (not hidden)."""
        attr = self._attr_map.get(name) or self._attr_name(name)
        try:
            from nemo_oo_agents.agentdoc import spec

            spec(self._agent, attr, hidden=False)
        except Exception:
            logger.debug("Failed to unhide skill %s (attr=%s)", name, attr, exc_info=True)

    def _hide_skill(self, name: str) -> None:
        """Mark a skill attribute as hidden."""
        attr = self._attr_map.get(name) or self._attr_name(name)
        try:
            from nemo_oo_agents.agentdoc import spec

            spec(self._agent, attr, hidden=True)
        except Exception:
            logger.debug("Failed to hide skill %s (attr=%s)", name, attr, exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _match(patterns: list[str], candidates: set[str]) -> set[str]:
        """Match patterns against candidate names using fnmatch."""
        matched: set[str] = set()
        for pattern in patterns:
            for candidate in candidates:
                if fnmatch.fnmatch(candidate, pattern):
                    matched.add(candidate)
        return matched


class _SkillEntry:
    """Internal record for a discovered skill."""

    __slots__ = ("name", "entry_point", "category")

    def __init__(self, name: str, entry_point: Any, category: str) -> None:
        self.name = name
        self.entry_point = entry_point
        self.category = category
