"""Tests for nemo_oo_agents.Skill — path-based loading."""

import math

import pytest

from nemo_oo_agents import Skill, TextSkill


@pytest.fixture
def skill_dir(tmp_path):
    d = tmp_path / "git-workflow"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: git-workflow\ndescription: Best practices for Git\n---\n"
        "# Git Workflow Guide\n\n1. Always create feature branches\n"
    )
    return d


# ── constructor paths ──────────────────────────────────────────────────────────


def test_skill_path_loads_id(skill_dir):
    assert TextSkill(path=skill_dir).id == "git-workflow"


def test_skill_path_creates_dynamic_subclass(skill_dir):
    skill = TextSkill(path=skill_dir)
    assert type(skill).__name__ != "Skill"
    assert "Best practices for Git" in (type(skill).__doc__ or "")


def test_skill_content_constructor():
    skill = Skill(content="A helpful skill.")
    assert "A helpful skill." in (type(skill).__doc__ or "")


def test_skill_obj_constructor():
    skill = Skill(math)
    assert isinstance(skill, Skill)


def test_skill_no_args_raises():
    with pytest.raises(ValueError, match="requires one of"):
        Skill()


def test_skill_multiple_args_raises():
    with pytest.raises(ValueError, match="exactly one of"):
        Skill(math, content="extra")


def test_skill_path_raises_for_missing_skill_md(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(ValueError, match=r"SKILL\.md not found"):
        TextSkill(path=d)


# ── properties ────────────────────────────────────────────────────────────────


def test_skill_description_property(skill_dir):
    assert TextSkill(path=skill_dir).description == "Best practices for Git"


def test_skill_dir_forwards_to_wrapped_obj():
    skill = Skill(math)
    assert "sqrt" in dir(skill)


def test_skill_dir_on_path_skill(skill_dir):
    # Non-obj skill: __dir__ falls back to base (no _skill_obj)
    skill = TextSkill(path=skill_dir)
    assert "id" in dir(skill)


# ── run_script ────────────────────────────────────────────────────────────────


@pytest.fixture
def skill_with_scripts(skill_dir):
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "greet.py").write_text("print('hello from script')")
    (scripts_dir / "echo_args.py").write_text("import sys\nprint(' '.join(sys.argv[1:]))")
    (scripts_dir / "fail.py").write_text("import sys\nprint('oops')\nsys.exit(1)")
    shebang = scripts_dir / "shebang_echo.py"
    shebang.write_text("#!/usr/bin/env python3\nimport sys\nprint(' '.join(sys.argv[1:]))")
    shebang.chmod(0o755)
    return skill_dir


@pytest.mark.asyncio
async def test_run_script_python(skill_with_scripts):
    output = await TextSkill(path=skill_with_scripts).run_script("greet.py", interpreter="python3")
    assert "hello from script" in output


@pytest.mark.asyncio
async def test_run_script_with_args(skill_with_scripts):
    output = await TextSkill(path=skill_with_scripts).run_script(
        "echo_args.py", "foo", "bar", interpreter="python3"
    )
    assert "foo bar" in output


@pytest.mark.asyncio
async def test_run_script_nonzero_exit_in_output(skill_with_scripts):
    output = await TextSkill(path=skill_with_scripts).run_script("fail.py", interpreter="python3")
    assert "oops" in output
    assert "exit code: 1" in output


@pytest.mark.asyncio
async def test_run_script_raises_for_missing_script(skill_dir):
    with pytest.raises(FileNotFoundError):
        await TextSkill(path=skill_dir).run_script("nonexistent.py")


@pytest.mark.asyncio
async def test_run_script_raises_when_script_is_a_directory(skill_with_scripts):
    # Create scripts/mydir/ — exists but is not a file → triggers the "available" error path
    (skill_with_scripts / "scripts" / "mydir").mkdir()
    with pytest.raises(FileNotFoundError, match="Available"):
        await TextSkill(path=skill_with_scripts).run_script("mydir")


@pytest.mark.asyncio
async def test_run_script_with_shebang_and_args(skill_with_scripts):
    # No interpreter= — uses shebang directly; covers _build_script_command no-interpreter+args path
    output = await TextSkill(path=skill_with_scripts).run_script(
        "shebang_echo.py", "hello", "world"
    )
    assert "hello world" in output


@pytest.mark.asyncio
async def test_run_script_raises_for_path_traversal(skill_with_scripts):
    with pytest.raises(ValueError, match="escapes the skill directory"):
        await TextSkill(path=skill_with_scripts).run_script("../../etc/passwd")


# ── read_file ─────────────────────────────────────────────────────────────────


def test_read_file_returns_content(skill_dir):
    assert "Best practices for Git" in TextSkill(path=skill_dir).read_file("SKILL.md")


def test_read_file_raises_for_path_traversal(skill_dir):
    with pytest.raises(ValueError, match="escapes the skill directory"):
        TextSkill(path=skill_dir).read_file("../../etc/passwd")


def test_read_file_raises_for_missing_file(skill_dir):
    with pytest.raises(FileNotFoundError):
        TextSkill(path=skill_dir).read_file("nonexistent.txt")


def test_read_file_raises_when_path_is_directory(skill_dir):
    (skill_dir / "subdir").mkdir()
    with pytest.raises(ValueError, match="is not a file"):
        TextSkill(path=skill_dir).read_file("subdir")
