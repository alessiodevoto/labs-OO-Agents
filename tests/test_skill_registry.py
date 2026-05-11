# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SkillRegistry — entry-point discovery, load, activate lifecycle."""

from unittest.mock import MagicMock, patch

import pytest

from nemo_oo_agents.skill import Skill
from nemo_oo_agents.skill_registry import SkillRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeSkill(Skill):
    """A test skill with no constructor args."""

    def do_thing(self) -> str:
        return "done"


class _FakeAgent:
    pass


@pytest.fixture
def agent():
    return _FakeAgent()


@pytest.fixture
def registry(agent):
    """SkillRegistry with no real entry points discovered."""
    with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[]):
        return SkillRegistry(agent)


# ---------------------------------------------------------------------------
# Tests: Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_discovered_empty_when_no_entry_points(self, registry):
        assert registry.discovered() == []

    def test_discovered_finds_entry_points(self, agent):
        ep = MagicMock()
        ep.name = "nemo.shell"
        with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[ep]):
            reg = SkillRegistry(agent)
        assert "nemo.shell" in reg.discovered()

    def test_discovered_handles_exception(self, agent):
        with patch(
            "nemo_oo_agents.skill_registry.entry_points",
            side_effect=Exception("broken"),
        ):
            reg = SkillRegistry(agent)
        assert reg.discovered() == []


# ---------------------------------------------------------------------------
# Tests: Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_adds_to_loaded(self, registry):
        skill = FakeSkill()
        registry.register("nemo.test", skill)
        assert "nemo.test" in registry.loaded()

    def test_register_adds_to_discovered(self, registry):
        skill = FakeSkill()
        registry.register("custom.tool", skill)
        assert "custom.tool" in registry.discovered()


# ---------------------------------------------------------------------------
# Tests: Loading
# ---------------------------------------------------------------------------


class TestLoading:
    def test_load_from_entry_point(self, agent):
        ep = MagicMock()
        ep.name = "nemo.fake"
        ep.load.return_value = FakeSkill

        with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[ep]):
            reg = SkillRegistry(agent)

        reg.load(["nemo.fake"])
        assert "nemo.fake" in reg.loaded()
        assert hasattr(agent, "fake")
        assert isinstance(agent.fake, FakeSkill)

    def test_load_glob_pattern(self, agent):
        ep1 = MagicMock()
        ep1.name = "nemo.a"
        ep1.load.return_value = FakeSkill
        ep2 = MagicMock()
        ep2.name = "nemo.b"
        ep2.load.return_value = FakeSkill

        with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[ep1, ep2]):
            reg = SkillRegistry(agent)

        reg.load(["nemo.*"])
        assert "nemo.a" in reg.loaded()
        assert "nemo.b" in reg.loaded()

    def test_load_skip_non_skill(self, agent):
        ep = MagicMock()
        ep.name = "nemo.bad"
        ep.load.return_value = "not a skill"

        with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[ep]):
            reg = SkillRegistry(agent)

        reg.load(["nemo.bad"])
        assert "nemo.bad" not in reg.loaded()


# ---------------------------------------------------------------------------
# Tests: Activation
# ---------------------------------------------------------------------------


class TestActivation:
    def test_activate_marks_as_activated(self, registry):
        skill = FakeSkill()
        registry.register("nemo.test", skill)
        registry.activate(["nemo.test"])
        assert "nemo.test" in registry.activated()

    def test_deactivate_removes_from_activated(self, registry):
        skill = FakeSkill()
        registry.register("nemo.test", skill)
        registry.activate(["nemo.test"])
        registry.deactivate(["nemo.test"])
        assert "nemo.test" not in registry.activated()

    def test_activate_glob(self, registry):
        registry.register("nemo.a", FakeSkill())
        registry.register("nemo.b", FakeSkill())
        registry.register("super.c", FakeSkill())
        registry.activate(["nemo.*"])
        assert "nemo.a" in registry.activated()
        assert "nemo.b" in registry.activated()
        assert "super.c" not in registry.activated()

    def test_activate_auto_loads(self, agent):
        ep = MagicMock()
        ep.name = "nemo.auto"
        ep.load.return_value = FakeSkill

        with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[ep]):
            reg = SkillRegistry(agent)

        reg.activate(["nemo.auto"])
        assert "nemo.auto" in reg.loaded()
        assert "nemo.auto" in reg.activated()
