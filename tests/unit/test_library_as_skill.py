# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``LibraryManager`` attaching user-defined ``Skill`` subclasses.

When an agent writes a library whose top-level ``__init__.py`` exports a
``Skill`` (either as a module-level ``skill`` instance or a ``Skill``
subclass), the library manager attaches *that instance* as
``agent.<lib_name>``. The bare module stays accessible on the agent's
module for normal imports.

Tests cover the four shapes:

- library with no Skill export → legacy ``LibrarySkill`` wrapper
- library with a ``Skill`` subclass defined in ``__init__.py``
- library with a module-level ``skill`` instance
- library whose Skill class requires constructor args (fallback to wrapper)
"""

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from nemo_oo_agents.library_manager import LibraryManager
from nemo_oo_agents.library_skill import LibrarySkill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_library(libs_root: Path, lib_name: str, init_body: str) -> Path:
    """Scaffold ``<libs_root>/<lib_name>`` as a tiny Python package."""
    lib_dir = libs_root / lib_name
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{lib_name}"\nversion = "0.1.0"\ndescription = "tmp"\ndependencies = []\n'
    )
    (lib_dir / "__init__.py").write_text(init_body)
    return lib_dir


def _make_agent() -> Any:
    """Build a minimal agent-like object with a dedicated module namespace."""
    # ``agent_module`` has to exist and be in sys.modules so
    # ``inspect.getmodule(type(agent))`` finds it and can set bare module
    # names (``stats`` in ``stats.percentile()``) on it.
    mod_name = "_nemo_test_agent_module"
    mod = ModuleType(mod_name)
    sys.modules[mod_name] = mod

    class _Agent:
        pass

    _Agent.__module__ = mod_name
    mod._Agent = _Agent  # type: ignore[attr-defined]
    return _Agent()


@pytest.fixture
def libs_root(tmp_path: Path, monkeypatch) -> Path:
    """Sandboxed libs root on sys.path; cleans up imports."""
    monkeypatch.syspath_prepend(str(tmp_path))
    # Remove anything we may have installed from a previous test
    for key in [k for k in sys.modules if k.startswith("test_lib_")]:
        del sys.modules[key]
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_library_without_skill_falls_back_to_wrapper(libs_root):
    """A plain utility module attaches as the legacy ``LibrarySkill``."""
    _make_library(
        libs_root,
        "test_lib_plain",
        '"""Plain stats helpers."""\n\ndef percentile(x):\n    return x[len(x) // 2]\n',
    )
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=libs_root)

    attached = agent.test_lib_plain
    assert isinstance(attached, LibrarySkill)
    # Bare module remains importable
    assert "test_lib_plain" in sys.modules
    assert sys.modules["test_lib_plain"].percentile([1, 2, 3]) == 2


def test_library_with_skill_subclass_attaches_instance(libs_root):
    """A library whose __init__ defines a Skill subclass surfaces as that class."""
    _make_library(
        libs_root,
        "test_lib_skill_sub",
        '''"""Custom skill lib."""
from nemo_oo_agents.skill import Skill


class MyThing(Skill):
    """My custom thing."""

    def greet(self) -> str:
        return "hello from MyThing"
''',
    )
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=libs_root)

    attached = agent.test_lib_skill_sub
    # Instance, not the wrapper:
    assert type(attached).__name__ == "MyThing"
    assert attached.greet() == "hello from MyThing"


def test_library_with_module_level_skill_instance(libs_root):
    """Exporting a module-level ``skill = ...`` instance wins — supports ctor args."""
    _make_library(
        libs_root,
        "test_lib_skill_inst",
        '''"""Preconfigured skill lib."""
from nemo_oo_agents.skill import Skill


class MyThing(Skill):
    def __init__(self, greeting: str):
        self.greeting = greeting

    def say(self) -> str:
        return self.greeting


skill = MyThing(greeting="configured")
''',
    )
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=libs_root)

    attached = agent.test_lib_skill_inst
    assert type(attached).__name__ == "MyThing"
    assert attached.say() == "configured"


def test_library_with_skill_needing_args_falls_back(libs_root):
    """A Skill subclass that can't instantiate without args → wrapper fallback."""
    _make_library(
        libs_root,
        "test_lib_needs_args",
        '''"""Skill needing ctor args."""
from nemo_oo_agents.skill import Skill


class NeedsArgs(Skill):
    def __init__(self, required):
        self.required = required
''',
    )
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=libs_root)

    attached = agent.test_lib_needs_args
    assert isinstance(attached, LibrarySkill)  # fallback


def test_bare_module_still_in_agent_module_namespace(libs_root):
    """The bare module is always reachable on the agent's module, regardless of
    whether the library exports a Skill or not — so ``lib.helper()`` keeps
    working in the exec namespace alongside ``self.lib``."""
    _make_library(
        libs_root,
        "test_lib_bare",
        '''"""Has both a skill and a free function."""
from nemo_oo_agents.skill import Skill


class MySk(Skill):
    def op(self): return "op"


def helper(): return "helper"
''',
    )
    agent = _make_agent()
    LibraryManager.install(agent, libs_dir=libs_root)

    # agent.<lib> is the Skill
    assert type(agent.test_lib_bare).__name__ == "MySk"

    # Module still accessible — via sys.modules and via agent_module
    import importlib

    mod = importlib.import_module("test_lib_bare")
    assert mod.helper() == "helper"
    assert mod.MySk().op() == "op"
