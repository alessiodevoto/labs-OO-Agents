"""Tests for SkillManager — discover and install Skills on an agent."""

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nemo_oo_agents import Skill, SkillManager
from nemo_oo_agents import skill_manager as _skill_manager_module


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
    with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.skill_manager"):
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
    with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.skill_manager"):
        skills = SkillManager.discover(tmp_path)
    assert skills == {}
    assert "failed to load SKILL.md" in caplog.text


# ── Python-file skills ────────────────────────────────────────────────────────


def _write_py_skill(
    base: Path, filename: str, class_name: str, docstring: str = "A python skill"
) -> Path:
    """Write a .py file in *base* containing a Skill subclass.

    Subclasses purposefully do NOT call ``super().__init__(content=...)`` —
    that reassigns ``self.__class__`` to a bare ``Skill`` and strips the
    subclass's methods (see Skill.__init__). Real-world Python skills keep
    their methods by omitting the content-init or wrapping an external obj.
    """
    path = base / filename
    path.write_text(
        f"""
from nemo_oo_agents.skill import Skill


class {class_name}(Skill):
    '''{docstring}'''

    def hello(self) -> str:
        return "hi from {class_name}"
"""
    )
    return path


def test_install_picks_up_python_skill_file(tmp_path):
    _write_py_skill(tmp_path, "my_skill.py", "MySkill", "my skill description")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert isinstance(agent.my_skill, Skill)
    assert agent.my_skill.hello() == "hi from MySkill"


def test_python_skill_attr_matches_filename_stem(tmp_path):
    _write_py_skill(tmp_path, "wtf_pm.py", "WtfProjectManagement")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert hasattr(agent, "wtf_pm")
    # Not the class-name version:
    assert not hasattr(agent, "wtf_project_management")


def test_python_skill_coexists_with_text_skills(tmp_path):
    _make_skill_dir(tmp_path, "text-skill", "A text skill")
    _write_py_skill(tmp_path, "py_skill.py", "PySkill")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert isinstance(agent.text_skill, Skill)
    assert isinstance(agent.py_skill, Skill)


def test_python_skill_skipped_if_attr_already_exists(tmp_path):
    _write_py_skill(tmp_path, "existing.py", "Existing")
    sentinel = SampleSkill()
    agent = make_agent(existing=sentinel)
    SkillManager.install(agent, skills_dir=tmp_path)
    assert agent.existing is sentinel


def test_python_skill_file_without_skill_subclass_ignored(tmp_path):
    (tmp_path / "not_a_skill.py").write_text("x = 1\n")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert not hasattr(agent, "not_a_skill")


def test_python_skill_file_with_import_error_warned_and_skipped(tmp_path, caplog):
    (tmp_path / "broken.py").write_text("this is not valid python !!!\n")
    agent = make_agent()
    with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.skill_manager"):
        SkillManager.install(agent, skills_dir=tmp_path)
    assert not hasattr(agent, "broken")
    assert "broken" in caplog.text.lower()


def test_python_skill_with_required_args_warned_and_skipped(tmp_path, caplog):
    (tmp_path / "needs_args.py").write_text(
        """
from nemo_oo_agents.skill import Skill


class NeedsArgs(Skill):
    '''needs ctor args'''
    def __init__(self, required_arg):
        super().__init__(content="x")
        self.required = required_arg
"""
    )
    agent = make_agent()
    with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.skill_manager"):
        SkillManager.install(agent, skills_dir=tmp_path)
    assert not hasattr(agent, "needs_args")
    assert "needs_args" in caplog.text.lower()


def test_python_skill_ignores_base_skill_class(tmp_path):
    """Importing the base ``Skill`` class must not trigger a spurious install.

    Any file that does ``from nemo_oo_agents.skill import Skill`` would end
    up with the base class in its namespace. The discovery skips ``Skill``
    and ``TextSkill`` by identity so helper files don't auto-attach those.
    """
    (tmp_path / "has_import.py").write_text(
        """
from nemo_oo_agents.skill import Skill, TextSkill
# No own Skill subclass defined here.
x = 1
"""
    )
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert not hasattr(agent, "has_import")


def test_python_skill_reexport_is_installed(tmp_path):
    """A skill file can just re-export a class defined in another module.

    This is the shipping pattern for third-party packages: put
    ``skills-nemo/xxx.py`` with a one-liner ``from pkg.skill import MySkill``
    and the discovery picks it up under the filename stem.
    """
    # "Other module" — a separate .py that defines the class
    other = tmp_path / "_defs.py"
    other.write_text(
        """
from nemo_oo_agents.skill import Skill


class ExternalSkill(Skill):
    '''class defined elsewhere'''

    def greet(self):
        return "external"
"""
    )

    # Loader uses importlib on the file directly, so we have to import by
    # path. Easiest: two separate passes — first install of _defs is skipped
    # (leading underscore), second file imports from it via sys.path hack.
    reexport = tmp_path / "external.py"
    reexport.write_text(
        f"""
import importlib.util
spec = importlib.util.spec_from_file_location('_ext_defs', {str(other)!r})
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
ExternalSkill = _mod.ExternalSkill
"""
    )

    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert hasattr(agent, "external")
    assert agent.external.greet() == "external"


def test_python_skill_module_level_instance(tmp_path):
    """A module-level ``skill`` instance is used verbatim (supports ctor args)."""
    (tmp_path / "preconfigured.py").write_text(
        """
from nemo_oo_agents.skill import Skill


class MySkill(Skill):
    '''My skill'''

    def __init__(self, greeting):
        self.greeting = greeting

    def say(self):
        return self.greeting


skill = MySkill(greeting="hello there")
"""
    )
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert hasattr(agent, "preconfigured")
    assert agent.preconfigured.say() == "hello there"


def test_python_skill_file_with_multiple_skill_subclasses_uses_first(tmp_path, caplog):
    (tmp_path / "many.py").write_text(
        """
from nemo_oo_agents.skill import Skill


class FirstSkill(Skill):
    '''first'''
    def __init__(self):
        super().__init__(content="first")


class SecondSkill(Skill):
    '''second'''
    def __init__(self):
        super().__init__(content="second")
"""
    )
    agent = make_agent()
    with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.skill_manager"):
        SkillManager.install(agent, skills_dir=tmp_path)
    assert hasattr(agent, "many")
    # Warned about extras
    assert "many" in caplog.text.lower()


def test_dunder_init_py_not_treated_as_skill(tmp_path, caplog):
    """A skills dir containing an __init__.py must not try to import it."""
    (tmp_path / "__init__.py").write_text("")
    agent = make_agent()
    with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.skill_manager"):
        SkillManager.install(agent, skills_dir=tmp_path)
    # __init__.py must not trigger any "skipped" warnings — it should be
    # filtered out before we ever try to import it.
    assert "__init__" not in caplog.text


def test_leading_underscore_py_files_ignored(tmp_path):
    """Files whose names start with _ are treated as private helpers, not skills."""
    _write_py_skill(tmp_path, "_helper.py", "Helper")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert not hasattr(agent, "_helper")


def test_discover_includes_python_skills(tmp_path):
    _write_py_skill(tmp_path, "tool_x.py", "ToolX", "tool X")
    skills = SkillManager.discover(tmp_path)
    assert "tool_x" in skills
    assert isinstance(skills["tool_x"], Skill)


def test_python_skill_attr_name_sanitises_hyphens(tmp_path):
    """A file named my-skill.py attaches as agent.my_skill (valid identifier)."""
    _write_py_skill(tmp_path, "my-skill.py", "MySkill")
    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert hasattr(agent, "my_skill")


def test_python_skill_same_stem_in_two_dirs_does_not_collide(tmp_path):
    """Two ``my_skill.py`` files in different dirs don't overwrite each
    other's sys.modules entry, and the first-wins semantics holds."""
    import sys

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    # dir_a defines the skill — this one should win (first scanned)
    _write_py_skill(dir_a, "dup.py", "FromA")
    # dir_b also defines a skill with the same file name
    _write_py_skill(dir_b, "dup.py", "FromB")

    agent = make_agent()
    SkillManager.install(agent, skills_dir=[dir_a, dir_b])

    # attr is attached from dir_a (first dir scanned)
    assert type(agent.dup).__name__ == "FromA"
    # And nothing claiming to be a skill module from our loader lingers
    # in sys.modules (we pop after extracting the instance).
    assert not any(k.startswith("_nemo_oo_skill_dup_") for k in sys.modules)


def test_python_skill_locally_defined_wins_over_reexport(tmp_path):
    """When a file both imports a Skill and defines its own, the local
    class wins — matches what the user almost always means."""
    # External file defining ExternalSkill — referenced via sys.path hack.
    (tmp_path / "_ext.py").write_text(
        """
from nemo_oo_agents.skill import Skill


class ExternalSkill(Skill):
    '''external — imported, not local'''
    def which(self) -> str:
        return "external"
"""
    )

    (tmp_path / "mixed.py").write_text(
        f"""
import importlib.util
spec = importlib.util.spec_from_file_location('_ext_mod', {str(tmp_path / "_ext.py")!r})
_m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_m)
ExternalSkill = _m.ExternalSkill     # re-exported

from nemo_oo_agents.skill import Skill


class LocalSkill(Skill):
    '''local — defined here, should win'''
    def which(self) -> str:
        return "local"
"""
    )

    agent = make_agent()
    SkillManager.install(agent, skills_dir=tmp_path)
    assert agent.mixed.which() == "local"
