# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SkillRegistry — discover, load, and activate skills on an agent."""

import fnmatch
import importlib.util
import inspect
import logging
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from nemo_oo_agents.skill import Skill, TextSkill

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


# ---------------------------------------------------------------------------
# Skill loading utilities (formerly in skill_manager.py)
# ---------------------------------------------------------------------------


def skill_from_module(module: Any, module_name: str, source: str = "") -> "Skill | None":
    """Extract a ``Skill`` instance from an already-imported module.

    Resolution order:

    1. Module-level ``skill`` attribute that is a ``Skill`` instance.
    2. First ``Skill`` subclass *defined in this module*
       (``cls.__module__`` equals ``module_name``).
    3. First re-exported ``Skill`` subclass in the module namespace.

    Returns ``None`` when none found, or when the class can't be
    instantiated without args. All failures log at WARNING.
    """
    explicit = vars(module).get("skill")
    if isinstance(explicit, Skill):
        return explicit

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


def _load_python_skill(path: Path) -> "Skill | None":
    """Import *path* and extract a ``Skill`` via :func:`skill_from_module`."""
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
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _is_python_skill_file(entry: Path) -> bool:
    """True if *entry* is a candidate Python skill file."""
    return entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_")


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
        self._lib_paths: dict[str, str] = {}  # lib_name → sys.path entry added for it
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

    def discover_libs(self, libs_path: "Path") -> None:
        """Scan a libs directory and register each skill package.

        Each subdirectory containing a pyproject.toml is imported and
        registered. The Skill subclass from __init__.py is used if found;
        otherwise a Skill(module) fallback wraps the module.

        The registry name is read from the pyproject.toml entry_points
        (``[project.entry-points."nemo_oo_agents.skills"]``). If no
        entry_point is declared, falls back to ``local.<lib_name>``.

        Args:
            libs_path: Directory containing library packages.
        """
        from pathlib import Path

        libs_path = Path(libs_path)
        if not libs_path.is_dir():
            return
        for lib_dir in sorted(libs_path.iterdir()):
            if not (lib_dir.is_dir() and (lib_dir / "pyproject.toml").exists()):
                continue
            lib_name = lib_dir.name
            reg_name = self._read_skill_name(lib_dir, lib_name)
            if reg_name in self._loaded:
                continue
            try:
                skill = self._import_lib(lib_dir, lib_name)
                if skill is not None:
                    self.register(reg_name, skill)
            except Exception:
                logger.warning("Library %s skipped", lib_name, exc_info=True)

    @staticmethod
    def _read_skill_name(lib_dir: "Path", lib_name: str) -> str:
        """Read the skill registry name from pyproject.toml entry_points.

        Falls back to ``local.<lib_name>`` if no entry_point is declared.
        """
        import tomllib

        pyproject = lib_dir / "pyproject.toml"
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            eps = data.get("project", {}).get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
            if eps:
                # Use the first declared entry_point name
                return next(iter(eps))
        except Exception:
            pass
        return f"local.{lib_name}"

    def _import_lib(self, lib_dir: "Path", lib_name: str) -> "Skill | None":
        """Import a library package and extract its Skill instance."""
        import importlib
        import sys as _sys

        libs_str = str(lib_dir.parent)
        added_to_path = False
        if libs_str not in _sys.path:
            _sys.path.insert(0, libs_str)
            added_to_path = True

        prefix = lib_name + "."
        for key in [k for k in _sys.modules if k == lib_name or k.startswith(prefix)]:
            del _sys.modules[key]

        try:
            module = importlib.import_module(lib_name)
        except Exception:
            if added_to_path:
                _sys.path.remove(libs_str)
            raise

        # Track the path addition so it can be cleaned up if the skill is unloaded
        if added_to_path:
            self._lib_paths[lib_name] = libs_str

        return skill_from_module(module, lib_name, source=f"Library {lib_name!r}")

    def discover_skills_dirs(self, dirs: "list[Path]") -> None:
        """Scan skills directories for TextSkills and Python skills.

        TextSkills (SKILL.md directories) register as cmd.<skill_id>.
        Python skills (.py files with Skill subclass) register as ext.<name>.

        Args:
            dirs: List of directories to scan.
        """
        from pathlib import Path

        for skills_dir in dirs:
            skills_dir = Path(skills_dir)
            if not skills_dir.is_dir():
                continue
            for entry in skills_dir.iterdir():
                if entry.is_dir():
                    skill_md = entry / "SKILL.md"
                    if not skill_md.exists():
                        skill_md = entry / "skill.md"
                    if skill_md.exists():
                        self._register_text_skill(entry)
                elif entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                    self._register_python_skill(entry)

    def _register_text_skill(self, entry: "Path") -> None:
        """Register a TextSkill from a SKILL.md directory."""
        from nemo_oo_agents.skill import TextSkill

        try:
            skill = TextSkill(path=entry)
            reg_name = f"cmd.{skill.id}"
            if reg_name not in self._loaded:
                self.register(reg_name, skill)
        except Exception:
            logger.warning("TextSkill %s skipped", entry, exc_info=True)

    def _register_python_skill(self, entry: "Path") -> None:
        """Register a Python skill from a .py file."""

        try:
            skill = _load_python_skill(entry)
            if skill is not None:
                name = entry.stem.replace("-", "_").replace(" ", "_")
                reg_name = f"ext.{name}"
                if reg_name not in self._loaded:
                    self.register(reg_name, skill)
        except Exception:
            logger.warning("Python skill %s skipped", entry, exc_info=True)

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
                if hasattr(self._agent, attr_name) and attr_name not in {
                    v for v in self._attr_map.values()
                }:
                    logger.warning(
                        "Skill %s overwrites existing agent attr '%s'",
                        name, attr_name,
                    )
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
            # Accept any object (duck-typing) — tools like ShellTools may be
            # mocked in tests or may not inherit from Skill in all contexts.
            if kwargs:
                raise TypeError("Cannot pass kwargs with a pre-constructed instance")
            skill = skill_or_cls

        attr = self._attr_name(name)
        if attr.startswith("_") or attr in _RESERVED_ATTRS:
            raise ValueError(f"Cannot register skill with reserved attr name {attr!r}")
        if hasattr(self._agent, attr) and attr not in self._attr_map.values():
            logger.warning("Skill %s overwrites existing agent attr '%s'", name, attr)
        setattr(self._agent, attr, skill)
        if hasattr(skill, "attach"):
            existing_agent = getattr(skill, "_agent", None)
            if existing_agent is not None and existing_agent is not self._agent:
                if hasattr(skill, "detach"):
                    skill.detach()
            if existing_agent is not self._agent:
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

        # Refresh slash commands so the TUI picks up @slash_command methods
        if matched:
            cmd_reg = getattr(self._agent, "_command_registry", None)
            if cmd_reg is not None and hasattr(cmd_reg, "refresh_skill_commands"):
                try:
                    cmd_reg.refresh_skill_commands()
                except Exception:
                    pass

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
        import importlib
        import sys as _sys

        mod_name = type(skill).__module__
        # For package modules (e.g. "excalidraw.__init__"), use the top-level package
        top_pkg = mod_name.split(".")[0]
        if top_pkg not in _sys.modules:
            return f"Module {mod_name} not in sys.modules — cannot reload"
        try:
            # Clear all submodules so reimport gets fresh code from disk
            prefix = top_pkg + "."
            for key in [k for k in _sys.modules if k == top_pkg or k.startswith(prefix)]:
                del _sys.modules[key]
            mod = importlib.import_module(top_pkg)
            # Find the Skill subclass in the reloaded module
            from nemo_oo_agents.skill import Skill as _Skill

            for obj in vars(mod).values():
                if isinstance(obj, type) and issubclass(obj, _Skill) and obj is not _Skill:
                    new_skill = obj()
                    setattr(self._agent, attr, new_skill)
                    new_skill.attach(self._agent)
                    # Refresh slash commands so CommandRegistry picks up new methods
                    cmd_reg = getattr(self._agent, "_command_registry", None)
                    if cmd_reg is not None and hasattr(cmd_reg, "refresh_skill_commands"):
                        try:
                            cmd_reg.refresh_skill_commands()
                        except Exception:
                            pass
                    return f"Reloaded {name} (self.{attr})"
            return f"Reloaded module {top_pkg} but no Skill subclass found"
        except Exception as e:
            return f"Reload failed for {name}: {e}"

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
    # Status (context block rendering)
    # ------------------------------------------------------------------

    def status(self) -> str:
        """Render the skills context block for the LLM.

        Shows active tools and available-but-inactive skills with one-liners.
        Excludes cmd.* (slash commands) since the agent cannot invoke them.
        """
        lines: list[str] = []

        # Active skills (excluding cmd.*)
        active_tools: list[tuple[str, str, str]] = []  # (name, attr, one_liner)
        for name in sorted(self._activated):
            if name.startswith("cmd."):
                continue
            attr = self._attr_map.get(name, "")
            skill = getattr(self._agent, attr, None) if attr else None
            if skill is None:
                continue
            doc_str = type(skill).__doc__ or ""
            one_liner = doc_str.strip().split("\n")[0][:65]
            active_tools.append((name, attr, one_liner))

        if active_tools:
            lines.append("Active Skills (use via self.<attr>, docs with doc(self.<attr>)):")
            for _name, attr, desc in active_tools:
                lines.append(f"  self.{attr:18s} {desc}")

        # Available but not activated (excluding cmd.*)
        available: list[tuple[str, str]] = []  # (name, one_liner)
        for name in sorted(set(self._discovered.keys()) - self._activated):
            if name.startswith("cmd."):
                continue
            # Try to get one-liner from loaded skill or entry point
            attr = self._attr_map.get(name, "")
            skill = getattr(self._agent, attr, None) if attr else None
            if skill:
                doc_str = type(skill).__doc__ or ""
                one_liner = doc_str.strip().split("\n")[0][:65]
            else:
                entry = self._discovered.get(name)
                if entry and entry.entry_point:
                    try:
                        cls = entry.entry_point.load()
                        doc_str = cls.__doc__ or ""
                        one_liner = doc_str.strip().split("\n")[0][:65]
                    except Exception:
                        one_liner = ""
                else:
                    one_liner = ""
            available.append((name, one_liner))

        if available:
            if active_tools:
                lines.append("")
            lines.append("Available Skills (activate with self.skills.activate(['name'])):")
            for name, desc in available:
                lines.append(f"  {name:28s} {desc}")

        return "\n".join(lines)

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
