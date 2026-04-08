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
    reg.get_completions.return_value = {
        "/help": "Show all commands",
        "/edit": "Edit a file",
        "/session resume": "Resume a session",
        "/session delete": "Delete a session",
        "/session list": "List sessions",
        "/python on": "Enable Python display",
        "/python off": "Disable Python display",
        "/exit": "Exit",
    }
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
    assert "!python" in texts
    assert "!ipython" in texts


def test_bang_partial(completer):
    items = completer.complete("!py")
    texts = [i.text for i in items]
    assert "!python" in texts
    assert "!ipython" not in texts


# ---------------------------------------------------------------------------
# No completions for regular text
# ---------------------------------------------------------------------------


def test_regular_text_no_completions(completer):
    assert completer.complete("hello") == []
    assert completer.complete("") == []
    assert completer.complete("how are you") == []
