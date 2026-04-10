"""Tests for the shared completion engine (completer.py).

Verifies that both TUI and web get identical completion behavior.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nemo_oo_agents_cli.tui.completer import Completer


@pytest.fixture
def mock_registry():
    reg = MagicMock()
    _completions = {
        "/help": "Show all commands",
        "/edit": "Edit a file",
        "/session resume": "Resume a session",
        "/session delete": "Delete a session",
        "/session list": "List sessions",
        "/python on": "Enable Python display",
        "/python off": "Disable Python display",
        "/exit": "Exit",
    }
    # The completer uses get_active_help() (keys may include argument hints).
    # For built-in commands there are no hints, so this is the same dict.
    reg.get_active_help.return_value = _completions
    reg.get_completions.return_value = _completions
    return reg


@pytest.fixture
def completer(mock_registry):
    return Completer(registry=mock_registry)


# ---------------------------------------------------------------------------
# Slash command completion
# ---------------------------------------------------------------------------


def test_slash_prefix_returns_all_commands(completer):
    items = completer.complete("/")
    texts = [i.text for i in items]
    assert "/help" in texts
    assert "/edit" in texts
    assert "/exit" in texts


def test_slash_partial_filters(completer):
    items = completer.complete("/he")
    assert len(items) == 1
    assert items[0].text == "/help"
    assert items[0].description == "Show all commands"


def test_slash_session_partial(completer):
    items = completer.complete("/session ")
    texts = [i.text for i in items]
    assert "/session resume" in texts
    assert "/session delete" in texts
    assert "/session list" in texts


def test_slash_case_insensitive(completer):
    items = completer.complete("/HE")
    assert len(items) == 1
    assert items[0].text == "/help"


# ---------------------------------------------------------------------------
# Path completion (/edit)
# ---------------------------------------------------------------------------


def test_edit_path_completion(completer):
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some files
        Path(tmpdir, "foo.py").touch()
        Path(tmpdir, "bar.txt").touch()
        Path(tmpdir, "subdir").mkdir()

        items = completer.complete(f"/edit {tmpdir}/")
        texts = [i.text for i in items]
        displays = [i.display for i in items]

        assert any("foo.py" in t for t in texts)
        assert any("bar.txt" in t for t in texts)
        assert any("subdir/" in d for d in displays)

        # Every item.text must start with "/edit " so the web UI
        # can use it as a full input replacement without losing the command.
        for item in items:
            assert item.text.startswith("/edit "), (
                f"Path completion item should be a full replacement: {item.text!r}"
            )


def test_edit_path_partial_filter(completer):
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "alpha.py").touch()
        Path(tmpdir, "beta.py").touch()

        items = completer.complete(f"/edit {tmpdir}/al")
        texts = [i.text for i in items]
        assert len(items) == 1
        assert any("alpha.py" in t for t in texts)


# ---------------------------------------------------------------------------
# Bang commands
# ---------------------------------------------------------------------------


def test_bang_prefix_returns_builtins(completer):
    items = completer.complete("!")
    texts = [i.text for i in items]
    # !python was removed; the builtins are now !ipython and !bash
    assert "!ipython" in texts
    assert "!bash" in texts


def test_bang_partial(completer):
    # "!ip" prefix uniquely matches !ipython
    items = completer.complete("!ip")
    texts = [i.text for i in items]
    assert "!ipython" in texts
    assert "!bash" not in texts


# ---------------------------------------------------------------------------
# No completions for regular text
# ---------------------------------------------------------------------------


def test_regular_text_no_completions(completer):
    assert completer.complete("hello") == []
    assert completer.complete("") == []
    assert completer.complete("how are you") == []


# ---------------------------------------------------------------------------
# User-invocable skills in completer — real registry, not mock
# ---------------------------------------------------------------------------


@pytest.fixture
def user_skill_registry(tmp_path):
    """A real CommandRegistry with one install-as:command skill."""
    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui.commands import CommandRegistry

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: My test skill\n"
        "argument-hint: <action>\ninstall-as: command\n---\nDo the thing.\n"
    )

    agent = MagicMock()
    agent.get_summarization_status = MagicMock(
        return_value={
            "active_events": 0,
            "policy": "auto",
            "has_summarizer": False,
            "max_tokens": 100000,
            "current_tokens": 0,
            "preserve_recent": 5,
            "summary_count": 0,
            "summary_tags": [],
        }
    )
    config = MagicMock()
    config.default_model = "test-model"
    frontend = MagicMock()

    return CommandRegistry(
        config=config,
        agent=agent,
        frontend=frontend,
        skills_dirs=[tmp_path],
        mcp_file=None,
    )


def test_user_skill_appears_in_completer_via_real_registry(user_skill_registry):
    """User skill shows up in Completer.complete() with a REAL registry, not a mock.

    This catches the gap where registry.get_completions() works but the skill
    fails to reach the Tab-completion dropdown in practice.
    """
    completer = Completer(registry=user_skill_registry)
    items = completer.complete("/")
    texts = [i.text for i in items]
    assert any("myskill" in t for t in texts), f"Expected '/myskill' in completions, got: {texts}"


def test_user_skill_partial_completion(user_skill_registry):
    """Partial typing '/mys' narrows to the user skill."""
    completer = Completer(registry=user_skill_registry)
    items = completer.complete("/mys")
    texts = [i.text for i in items]
    assert any("myskill" in t for t in texts)
    # Built-in commands like /model don't start with /mys
    assert not any("model" in t for t in texts)


def test_opted_out_skill_not_in_completer(tmp_path):
    """A skill with user-invocable: false does NOT appear in Tab completions.

    CC default: user-invocable is true. Opt out with user-invocable: false.
    """
    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui.commands import CommandRegistry

    skill_dir = tmp_path / "internal"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: internal\ndescription: Internal tool\nuser-invocable: false\n---\nBody.\n"
    )

    agent = MagicMock()
    config = MagicMock()
    config.default_model = "test"
    frontend = MagicMock()

    registry = CommandRegistry(
        config=config,
        agent=agent,
        frontend=frontend,
        skills_dirs=[tmp_path],
        mcp_file=None,
    )
    completer = Completer(registry=registry)
    items = completer.complete("/")
    texts = [i.text for i in items]
    assert not any("internal" in t for t in texts), (
        f"Opted-out skill should not appear in completions, but found in: {texts}"
    )


def _make_skill_registry(tmp_path, skill_name: str, skill_md_text: str):
    """Helper: create a CommandRegistry with a single skill."""
    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui.commands import CommandRegistry

    skill_dir = tmp_path / skill_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(skill_md_text)

    agent = MagicMock()
    agent.get_summarization_status = MagicMock(
        return_value={
            "active_events": 0,
            "policy": "auto",
            "has_summarizer": False,
            "max_tokens": 100000,
            "current_tokens": 0,
            "preserve_recent": 5,
            "summary_count": 0,
            "summary_tags": [],
        }
    )
    config = MagicMock()
    config.default_model = "test"
    frontend = MagicMock()

    return CommandRegistry(
        config=config,
        agent=agent,
        frontend=frontend,
        skills_dirs=[tmp_path],
        mcp_file=None,
    )


def test_bracket_hint_not_inserted(tmp_path):
    """[label] style argument hints must NOT be inserted when Tab-completing.

    /wtf-status has argument-hint: [label].  Selecting the completion should
    insert '/wtf-status' only — not '/wtf-status [label]'.
    """
    registry = _make_skill_registry(
        tmp_path,
        "wtf-status",
        "---\nname: wtf-status\ndescription: Show project status\n"
        "argument-hint: [label]\n---\nShow status.\n",
    )
    completer = Completer(registry=registry)
    items = completer.complete("/wtf-status")

    assert items, "Expected at least one completion for /wtf-status"
    item = next((i for i in items if "wtf-status" in i.text), None)
    assert item is not None, f"No wtf-status item found in: {[i.text for i in items]}"
    assert item.text == "/wtf-status", f"Hint must not be inserted; got text={item.text!r}"


def test_bracket_hint_shown_in_display(tmp_path):
    """[label] style argument hints must appear in the display (menu) string."""
    registry = _make_skill_registry(
        tmp_path,
        "wtf-status",
        "---\nname: wtf-status\ndescription: Show project status\n"
        "argument-hint: [label]\n---\nShow status.\n",
    )
    completer = Completer(registry=registry)
    items = completer.complete("/wtf")

    item = next((i for i in items if "wtf-status" in i.text), None)
    assert item is not None, f"No wtf-status item found in: {[i.text for i in items]}"
    assert "[label]" in item.display, (
        f"Hint should be visible in the dropdown; got display={item.display!r}"
    )


def test_angle_hint_shown_in_display(tmp_path):
    """<action> style argument hints must appear in the display (menu) string."""
    registry = _make_skill_registry(
        tmp_path,
        "wtf",
        "---\nname: wtf\ndescription: Manage issues\nargument-hint: <action>\n---\nManage.\n",
    )
    completer = Completer(registry=registry)
    items = completer.complete("/wtf")

    item = next((i for i in items if i.text == "/wtf"), None)
    assert item is not None, f"No /wtf item found in: {[i.text for i in items]}"
    assert "<action>" in item.display, (
        f"Hint should be visible in the dropdown; got display={item.display!r}"
    )
