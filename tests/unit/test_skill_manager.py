"""Tests for SkillManager — discover and install Skills on an agent."""

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent006 import Skill, SkillManager
from agent006 import skill_manager as _skill_manager_module


class SampleSkill(Skill):
    """Short description of sample skill."""


def make_agent(**skill_attrs: Any) -> Any:
    return SimpleNamespace(**skill_attrs)


def _make_skill_dir(base: Path, name: str, description: str = "A skill") -> Path:
    d = base / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\nContent.")
    return d


# ── install ───────────────────────────────────────────────────────────────────


def test_install_assigns_skill_as_agent_attr(tmp_path):
    _make_skill_dir(tmp_path, "git-workflow", "Git helpers")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert isinstance(agent.git_workflow, Skill)


def test_install_replaces_hyphens_with_underscores(tmp_path):
    _make_skill_dir(tmp_path, "my-cool-skill")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert hasattr(agent, "my_cool_skill")
    assert not hasattr(agent, "my-cool-skill")


def test_install_does_not_overwrite_existing_attr(tmp_path):
    _make_skill_dir(tmp_path, "git-workflow")
    existing = SampleSkill()
    agent = make_agent(git_workflow=existing)
    SkillManager.install(agent, skills_dir=tmp_path)
    assert agent.git_workflow is existing


def test_install_ignores_dirs_without_skill_md(tmp_path):
    (tmp_path / "not-a-skill").mkdir()
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert not hasattr(agent, "not_a_skill")


def test_install_accepts_string_path(tmp_path):
    _make_skill_dir(tmp_path, "git-workflow")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=str(tmp_path))
    assert isinstance(agent.git_workflow, Skill)


def test_install_accepts_list_of_paths(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _make_skill_dir(dir_a, "skill-one")
    _make_skill_dir(dir_b, "skill-two")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=[dir_a, dir_b])
    assert hasattr(agent, "skill_one")
    assert hasattr(agent, "skill_two")


def test_install_returns_manager_instance(tmp_path):
    agent = make_agent()
    manager = SkillManager.install(agent, skills_dir=tmp_path)
    assert isinstance(manager, SkillManager)
    assert manager.agent is agent


def test_install_skips_nonexistent_dir(tmp_path):
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path / "does-not-exist")
    # no crash, nothing assigned


def test_install_skips_malformed_skill_md(tmp_path, caplog, monkeypatch):
    _make_skill_dir(tmp_path, "bad-skill")

    def _bad_skill(**kwargs):
        raise RuntimeError("parse error")

    monkeypatch.setattr(_skill_manager_module, "TextSkill", _bad_skill)
    with caplog.at_level(logging.WARNING, logger="agent006.skill_manager"):
        SkillManager.install(make_agent(), skills_dir=tmp_path)
    assert "failed to load SKILL.md" in caplog.text


# ── discover ──────────────────────────────────────────────────────────────────


def test_discover_finds_skills(tmp_path):
    _make_skill_dir(tmp_path, "my-skill", "My skill")
    skills = SkillManager.discover(tmp_path)
    assert "my-skill" in skills
    assert isinstance(skills["my-skill"], Skill)


def test_discover_returns_empty_for_no_skills(tmp_path):
    assert SkillManager.discover(tmp_path) == {}


def test_discover_skips_malformed_skill_md(tmp_path, caplog, monkeypatch):
    _make_skill_dir(tmp_path, "bad-skill")

    def _bad_skill(*args, **kwargs):
        raise RuntimeError("parse error")

    monkeypatch.setattr(_skill_manager_module, "TextSkill", _bad_skill)
    with caplog.at_level(logging.WARNING, logger="agent006.skill_manager"):
        skills = SkillManager.discover(tmp_path)
    assert skills == {}
    assert "failed to load SKILL.md" in caplog.text
