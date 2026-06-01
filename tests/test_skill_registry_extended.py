# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SkillRegistry — file-based discovery, deps, libs, reload."""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nemo_oo_agents.skill import Skill
from nemo_oo_agents.skill_registry import SkillRegistry


class FakeSkill(Skill):
    """A test skill."""

    requires: tuple[str, ...] = ()


class DepSkill(Skill):
    """Skill that declares a dependency."""

    requires = ("nemo.base",)


class _FakeAgent:
    pass


@pytest.fixture
def agent():
    return _FakeAgent()


@pytest.fixture
def registry(agent):
    with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[]):
        return SkillRegistry(agent)


# ---------------------------------------------------------------------------
# Tests: discover_skills_dirs
# ---------------------------------------------------------------------------


class TestDiscoverSkillsDirs:
    def test_python_skill_file_discovered(self, registry, agent, tmp_path):
        """A .py file with a Skill subclass is discovered as ext.<name>."""
        skill_file = tmp_path / "my_tool.py"
        skill_file.write_text(
            textwrap.dedent("""
            from nemo_oo_agents.skill import Skill

            class MyTool(Skill):
                \"\"\"A custom tool.\"\"\"
                pass
        """)
        )
        registry.discover_skills_dirs([tmp_path])
        assert "ext.my_tool" in registry.loaded()
        assert hasattr(agent, "my_tool")

    def test_underscore_files_skipped(self, registry, tmp_path):
        """Files starting with _ are not loaded."""
        skill_file = tmp_path / "_private.py"
        skill_file.write_text(
            textwrap.dedent("""
            from nemo_oo_agents.skill import Skill
            class Priv(Skill): pass
        """)
        )
        registry.discover_skills_dirs([tmp_path])
        assert "ext._private" not in registry.loaded()

    def test_text_skill_discovered(self, registry, agent, tmp_path):
        """A directory with SKILL.md is discovered as cmd.<id>."""
        skill_dir = tmp_path / "my-cmd"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-cmd\ndescription: A test command\n---\nDo the thing.\n"
        )
        registry.discover_skills_dirs([tmp_path])
        assert "cmd.my-cmd" in registry.loaded()

    def test_nonexistent_dir_skipped(self, registry):
        """Non-existent directories are silently skipped."""
        registry.discover_skills_dirs([Path("/nonexistent/path")])
        # Should not raise

    def test_broken_python_file_skipped(self, registry, tmp_path):
        """A .py file that fails to import is skipped with warning."""
        skill_file = tmp_path / "broken.py"
        skill_file.write_text("raise RuntimeError('boom')")
        registry.discover_skills_dirs([tmp_path])
        assert "ext.broken" not in registry.loaded()


# ---------------------------------------------------------------------------
# Tests: discover_libs
# ---------------------------------------------------------------------------


class TestDiscoverLibs:
    def test_lib_with_pyproject_discovered(self, registry, agent, tmp_path):
        """A library with pyproject.toml and Skill subclass is registered."""
        lib_dir = tmp_path / "my_lib"
        lib_dir.mkdir()
        (lib_dir / "pyproject.toml").write_text(
            '[project]\nname = "my-lib"\n\n'
            '[project.entry-points."nemo_oo_agents.skills"]\n'
            '"local.my_lib" = "my_lib:MyLibSkill"\n'
        )
        (lib_dir / "__init__.py").write_text(
            "from nemo_oo_agents.skill import Skill\n\n"
            "class MyLibSkill(Skill):\n"
            '    """A library skill."""\n'
        )
        registry.discover_libs(tmp_path)
        assert "local.my_lib" in registry.loaded()

    def test_dir_without_pyproject_skipped(self, registry, tmp_path):
        """Directories without pyproject.toml are skipped."""
        lib_dir = tmp_path / "no_pyproject"
        lib_dir.mkdir()
        (lib_dir / "__init__.py").write_text("x = 1")
        registry.discover_libs(tmp_path)
        assert registry.loaded() == []

    def test_nonexistent_libs_path(self, registry):
        """Non-existent libs_path is silently handled."""
        registry.discover_libs(Path("/nonexistent"))
        # Should not raise

    def test_already_loaded_lib_skipped(self, registry, agent, tmp_path):
        """A library already loaded is not re-imported."""
        lib_dir = tmp_path / "dup_lib"
        lib_dir.mkdir()
        (lib_dir / "pyproject.toml").write_text(
            textwrap.dedent("""
            [project]
            name = "dup-lib"
        """)
        )
        (lib_dir / "__init__.py").write_text(
            textwrap.dedent("""
            from nemo_oo_agents.skill import Skill
            class DupSkill(Skill): pass
        """)
        )
        # Pre-register to simulate already loaded
        registry.register("local.dup_lib", FakeSkill())
        registry.discover_libs(tmp_path)
        # Should not have re-loaded (still the FakeSkill instance)
        assert isinstance(agent.dup_lib, FakeSkill)


# ---------------------------------------------------------------------------
# Tests: _resolve_deps
# ---------------------------------------------------------------------------


class TestResolveDeps:
    def test_resolves_single_dependency(self, agent):
        """A skill with requires=('nemo.base',) triggers loading of its dep."""
        ep_base = MagicMock()
        ep_base.name = "nemo.base"
        ep_base.load.return_value = FakeSkill

        with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[ep_base]):
            reg = SkillRegistry(agent)

        dep_skill = DepSkill()
        reg.register("nemo.dep", dep_skill)
        reg._resolve_deps("nemo.dep")
        assert "nemo.base" in reg.loaded()

    def test_cycle_detection(self, registry, agent):
        """Circular dependencies don't infinite-loop."""

        class CycleA(Skill):
            requires = ("nemo.cycle_b",)

        class CycleB(Skill):
            requires = ("nemo.cycle_a",)

        registry.register("nemo.cycle_a", CycleA())
        registry.register("nemo.cycle_b", CycleB())
        # Should not raise or loop forever
        registry._resolve_deps("nemo.cycle_a")

    def test_missing_dep_warns(self, registry, agent):
        """Missing dependency logs a warning but doesn't crash."""
        skill = DepSkill()  # requires ('nemo.base',)
        registry.register("nemo.needy", skill)
        # nemo.base is not discovered — should warn, not crash
        registry._resolve_deps("nemo.needy")
        assert "nemo.base" not in registry.loaded()


# ---------------------------------------------------------------------------
# Tests: reload
# ---------------------------------------------------------------------------


class TestReload:
    @pytest.mark.asyncio
    async def test_reload_not_loaded_returns_error(self, registry):
        """Reloading an unknown skill returns an error message."""
        result = await registry.reload("nemo.nonexistent")
        assert "not loaded" in result.lower() or "not in" in result.lower()

    @pytest.mark.asyncio
    async def test_reload_all_loaded(self, registry, agent):
        """reload() without args reloads all loaded skills."""
        registry.register("nemo.a", FakeSkill())
        registry.register("nemo.b", FakeSkill())
        result = await registry.reload()
        # Should attempt to reload both — result is a string summary
        assert isinstance(result, str)
