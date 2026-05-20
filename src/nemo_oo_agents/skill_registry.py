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

        self.skills.load(['nemo.*'])
        self.skills.activate(['nemo.shell', 'nemo.repo'])
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
                category=ep.name.split(".")[0] if "." in ep.name else "",
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

        Patterns support fnmatch globs: 'nemo.*', '*', 'nemo.shell'.
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
                attr_name = name.split(".")[-1] if "." in name else name
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

    def register(
        self, name: str, skill_or_cls: "Skill | type[Skill] | None" = None, /, **kwargs
    ) -> None:
        """Register a skill by name, assigning it as self.<leaf_name>.

        Three modes:
          register('nemo.shell', cwd='...')             — load class from entry points, construct with kwargs
          register('nemo.shell', ShellTools, cwd='..') — explicit class + kwargs
          register('nemo.shell', existing_instance)    — pre-constructed instance

        The leaf of `name` (after last '.') becomes the attr on the agent.
        """
        if skill_or_cls is None:
            entry = self._discovered.get(name)
            if entry is None or entry.entry_point is None:
                raise KeyError(f"Skill {name!r} not found in entry points")
            skill_or_cls = entry.entry_point.load()

        if isinstance(skill_or_cls, type) and issubclass(skill_or_cls, Skill):
            skill = skill_or_cls(**kwargs)
        elif isinstance(skill_or_cls, Skill):
            if kwargs:
                raise TypeError("Cannot pass kwargs with a pre-constructed Skill instance")
            skill = skill_or_cls
        else:
            raise TypeError(f"Expected Skill class or instance, got {type(skill_or_cls)}")

        attr = self._attr_name(name)
        if attr.startswith("_") or attr in _RESERVED_ATTRS:
            raise ValueError(f"Cannot register skill with reserved attr name {attr!r}")
        setattr(self._agent, attr, skill)
        if getattr(skill, "_agent", None) is None:
            skill.attach(self._agent)
        self._loaded.add(name)
        self._attr_map[name] = attr
        if name not in self._discovered:
            category = name.split(".")[0] if "." in name else ""
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
        attr = name.split(".")[-1] if "." in name else name
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
    # Reload
    # ------------------------------------------------------------------

    def reload(self, name: str | None = None) -> str:
        """Hot-reload one or all loaded skills.

        Re-imports the skill's module and re-attaches the updated instance.

        Args:
            name: Fully-qualified skill name (e.g. 'nemo.shell'). If None, reload all.

        Returns:
            Status message.
        """
        if name is not None:
            return self._reload_one(name)
        results = []
        for n in list(self._loaded):
            results.append(self._reload_one(n))
        return "\n".join(results)

    def _reload_one(self, name: str) -> str:
        """Reload a single skill by re-importing its module."""
        attr = self._attr_map.get(name)
        if attr is None:
            return f"Skill {name!r} is not loaded"
        skill = getattr(self._agent, attr, None)
        if skill is None:
            return f"Skill {name!r} has no instance on agent"
        # Try to reimport the module the skill class came from
        import importlib
        import sys as _sys

        mod_name = type(skill).__module__
        if mod_name in _sys.modules:
            try:
                mod = importlib.reload(_sys.modules[mod_name])
                # Find the Skill subclass in the reloaded module
                from nemo_oo_agents.skill import Skill as _Skill

                for obj in vars(mod).values():
                    if isinstance(obj, type) and issubclass(obj, _Skill) and obj is not _Skill:
                        new_skill = obj()
                        setattr(self._agent, attr, new_skill)
                        new_skill.attach(self._agent)
                        return f"Reloaded {name} (self.{attr})"
                return f"Reloaded module {mod_name} but no Skill subclass found"
            except Exception as e:
                return f"Reload failed for {name}: {e}"
        return f"Module {mod_name} not in sys.modules — cannot reload"

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def __getitem__(self, name: str) -> Any:
        """Access a skill by its fully-qualified registry name."""
        attr = self._attr_map.get(name)
        if attr is None:
            raise KeyError(f"Skill {name!r} is not loaded")
        return getattr(self._agent, attr)

    def __getattr__(self, name: str) -> Any:
        """Access skills by category namespace or leaf name.

        self.skills.nvidia.shell → self.skills['nvidia/shell']
        self.skills.shell → looks up any loaded skill with leaf 'shell'
        """
        # Avoid recursion on internal attrs
        if name.startswith("_"):
            raise AttributeError(name)
        # Check if it's a category prefix
        loaded = object.__getattribute__(self, "_loaded")
        attr_map = object.__getattribute__(self, "_attr_map")
        agent = object.__getattribute__(self, "_agent")

        categories = {n.split(".")[0] for n in loaded if "." in n}
        if name in categories:
            return _NamespaceProxy(self, name)
        # Check if it's a leaf name
        for reg_name in loaded:
            leaf = reg_name.split(".")[-1] if "." in reg_name else reg_name
            if leaf == name:
                attr = attr_map.get(reg_name, name)
                return getattr(agent, attr)
        raise AttributeError(f"No skill with name or category {name!r}")

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


class _NamespaceProxy:
    """Proxy for dotted namespace access: self.skills.nvidia.shell."""

    __slots__ = ("_registry", "_prefix")

    def __init__(self, registry: SkillRegistry, prefix: str) -> None:
        object.__setattr__(self, "_registry", registry)
        object.__setattr__(self, "_prefix", prefix)

    def __getattr__(self, name: str) -> Any:
        key = f"{object.__getattribute__(self, '_prefix')}.{name}"
        return object.__getattribute__(self, "_registry")[key]

    def __repr__(self) -> str:
        return f"<SkillNamespace: {object.__getattribute__(self, '_prefix')}>"


class _SkillEntry:
    """Internal record for a discovered skill."""

    __slots__ = ("name", "entry_point", "category")

    def __init__(self, name: str, entry_point: Any, category: str) -> None:
        self.name = name
        self.entry_point = entry_point
        self.category = category
