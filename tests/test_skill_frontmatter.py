"""Tests for SKILL.md frontmatter parsing (inlined from skills_ref)."""

import pytest

from nemo_oo_agents.skill import _find_skill_md, _parse_frontmatter, _read_skill_properties

# ---------------------------------------------------------------------------
# _find_skill_md
# ---------------------------------------------------------------------------


def test_find_skill_md_uppercase(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: foo\ndescription: bar\n---\nbody")
    assert _find_skill_md(tmp_path) == tmp_path / "SKILL.md"


def test_find_skill_md_lowercase_fallback(tmp_path):
    (tmp_path / "skill.md").write_text("---\nname: foo\ndescription: bar\n---\nbody")
    assert _find_skill_md(tmp_path) == tmp_path / "skill.md"


def test_find_skill_md_prefers_uppercase(tmp_path):
    (tmp_path / "SKILL.md").write_text("upper")
    (tmp_path / "skill.md").write_text("lower")
    assert _find_skill_md(tmp_path) == tmp_path / "SKILL.md"


def test_find_skill_md_missing(tmp_path):
    assert _find_skill_md(tmp_path) is None


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------


def test_parse_frontmatter_basic():
    content = "---\nname: my-skill\ndescription: Does stuff\n---\n# Body\nHello"
    meta, body = _parse_frontmatter(content)
    assert meta["name"] == "my-skill"
    assert meta["description"] == "Does stuff"
    assert body.strip() == "# Body\nHello"


def test_parse_frontmatter_no_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        _parse_frontmatter("just some text")


def test_parse_frontmatter_unclosed():
    with pytest.raises(ValueError, match="frontmatter"):
        _parse_frontmatter("---\nname: foo\n")


def test_parse_frontmatter_empty_body():
    content = "---\nname: x\ndescription: y\n---\n"
    meta, body = _parse_frontmatter(content)
    assert body.strip() == ""


def test_parse_frontmatter_optional_fields():
    content = "---\nname: x\ndescription: y\nlicense: MIT\nallowed-tools: Bash(*)\n---\nbody"
    meta, body = _parse_frontmatter(content)
    assert meta["license"] == "MIT"
    assert meta["allowed-tools"] == "Bash(*)"


def test_parse_frontmatter_metadata_dict():
    content = "---\nname: x\ndescription: y\nmetadata:\n  key: value\n  foo: bar\n---\nbody"
    meta, body = _parse_frontmatter(content)
    assert meta["metadata"] == {"key": "value", "foo": "bar"}


def test_parse_frontmatter_metadata_values_are_strings():
    content = "---\nname: x\ndescription: y\nmetadata:\n  count: 42\n---\nbody"
    meta, _ = _parse_frontmatter(content)
    # All metadata values must be strings
    assert meta["metadata"]["count"] == "42"


# ---------------------------------------------------------------------------
# _read_skill_properties
# ---------------------------------------------------------------------------


def test_read_skill_properties_basic(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: my-skill\ndescription: Does things\n---\nbody")
    props = _read_skill_properties(tmp_path)
    assert props.name == "my-skill"
    assert props.description == "Does things"


def test_read_skill_properties_missing_file(tmp_path):
    with pytest.raises(ValueError, match="SKILL.md not found"):
        _read_skill_properties(tmp_path)


def test_read_skill_properties_missing_name(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\ndescription: foo\n---\nbody")
    with pytest.raises(ValueError, match="name"):
        _read_skill_properties(tmp_path)


def test_read_skill_properties_missing_description(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: foo\n---\nbody")
    with pytest.raises(ValueError, match="description"):
        _read_skill_properties(tmp_path)


def test_read_skill_properties_optional_fields(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\nlicense: Apache-2.0\nallowed-tools: Bash(*)\n---\nbody"
    )
    props = _read_skill_properties(tmp_path)
    assert props.license == "Apache-2.0"
    assert props.allowed_tools == "Bash(*)"
    assert props.metadata == {}


def test_read_skill_properties_with_metadata(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\nmetadata:\n  env: prod\n---\nbody"
    )
    props = _read_skill_properties(tmp_path)
    assert props.metadata == {"env": "prod"}


# ---------------------------------------------------------------------------
# install-as / user-invocable / is_user_command
# ---------------------------------------------------------------------------


def test_install_as_command_is_user_command(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: wtf\ndescription: Issue manager\ninstall-as: command\n---\nbody"
    )
    props = _read_skill_properties(tmp_path)
    assert props.install_as == "command"
    assert props.is_user_command is True


def test_user_invocable_true_is_user_command(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\nuser-invocable: true\n---\nbody"
    )
    props = _read_skill_properties(tmp_path)
    assert props.user_invocable is True
    assert props.is_user_command is True


def test_user_invocable_false_is_not_user_command(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\nuser-invocable: false\n---\nbody"
    )
    props = _read_skill_properties(tmp_path)
    assert props.user_invocable is False
    assert props.is_user_command is False


def test_no_flags_is_user_command_by_default(tmp_path):
    """CC default: user-invocable is True. No flags → user command."""
    (tmp_path / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody")
    props = _read_skill_properties(tmp_path)
    assert props.is_user_command is True


def test_argument_hint_plain_string(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\nargument-hint: <action> [issue-id]\n---\nbody"
    )
    props = _read_skill_properties(tmp_path)
    assert props.argument_hint == "<action> [issue-id]"


def test_argument_hint_invalid_yaml_is_raw_string(tmp_path):
    """Values like '"<title>" [-p priority]' are invalid YAML but must parse as a string."""
    (tmp_path / "SKILL.md").write_text(
        '---\nname: x\ndescription: y\nargument-hint: "<title>" [-p priority] [-a assignee]\n---\nbody'
    )
    props = _read_skill_properties(tmp_path)
    assert props.argument_hint == '"<title>" [-p priority] [-a assignee]'


def test_argument_hint_yaml_list_coerced_to_string(tmp_path):
    """argument-hint: [label] is valid YAML (a list) but must be coerced to a string."""
    (tmp_path / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\nargument-hint: [label]\n---\nbody"
    )
    props = _read_skill_properties(tmp_path)
    assert isinstance(props.argument_hint, str)
