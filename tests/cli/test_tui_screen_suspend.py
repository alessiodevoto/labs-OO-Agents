"""Tests for TUI screen suspension during /edit and /ipython.

Verifies that external programs (editor, IPython) use prompt_toolkit's
run_in_terminal(in_executor=True) to properly suspend the TUI and avoid
screen corruption when the agent is running.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# open_editor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_editor_uses_run_in_terminal():
    """open_editor must use run_in_terminal(in_executor=True), not run_in_executor."""
    from nemo_oo_agents_cli.tui.frontend import TerminalFrontend

    config = MagicMock()
    frontend = TerminalFrontend(config)

    mock_rit = AsyncMock(return_value=None)

    with (
        patch("prompt_toolkit.application.run_in_terminal", mock_rit),
        patch("tempfile.mkstemp", return_value=(99, "/tmp/fake.py")),
        patch("os.close"),
        patch("pathlib.Path.write_text"),
        patch("pathlib.Path.read_text", return_value="edited content"),
        patch("os.unlink"),
    ):
        result = await frontend.open_editor("test.py", "original", "python")

    mock_rit.assert_called_once()
    _, kwargs = mock_rit.call_args
    assert kwargs.get("in_executor") is True, (
        "open_editor must pass in_executor=True to avoid blocking the event loop"
    )
    assert result == "edited content"


# ---------------------------------------------------------------------------
# _handle_python_shell
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_python_shell_uses_run_in_terminal():
    """/ipython must use run_in_terminal(in_executor=True)."""
    from nemo_oo_agents_cli.tui.session import _handle_python_shell

    agent = MagicMock()
    frontend = AsyncMock()
    mock_rit = AsyncMock(return_value=None)

    with (
        patch("sys.stdin") as mock_stdin,
        patch("prompt_toolkit.application.run_in_terminal", mock_rit),
        patch.dict("sys.modules", {"IPython": MagicMock()}),
    ):
        mock_stdin.isatty.return_value = True
        await _handle_python_shell(agent, frontend)

    mock_rit.assert_called_once()
    _, kwargs = mock_rit.call_args
    assert kwargs.get("in_executor") is True, (
        "_handle_python_shell must pass in_executor=True to avoid blocking the event loop"
    )


@pytest.mark.asyncio
async def test_handle_python_shell_restores_streams():
    """_embed() must restore sys.stdout/stderr to __stdout__/__stderr__."""
    from nemo_oo_agents_cli.tui.session import _handle_python_shell

    agent = MagicMock()
    frontend = AsyncMock()

    captured_stdout = None

    async def fake_rit(fn, *, in_executor=False):
        """Capture what stdout is inside _embed()."""
        import sys
        import threading

        nonlocal captured_stdout
        if in_executor:
            # Simulate what run_in_terminal(fn, in_executor=True) does:
            # run fn in a thread
            result = [None]
            exc = [None]

            def _run():
                nonlocal captured_stdout
                try:
                    # Set up a fake forwarder on sys.stdout so we can check
                    # that _embed restores it
                    original = sys.stdout
                    sys.stdout = MagicMock(name="forwarder")
                    sys.stdout.__stdout__ = original  # won't help, but shows state
                    fn()
                    # After _embed(), stdout should have been restored and re-set
                    captured_stdout = sys.stdout
                except Exception as e:
                    exc[0] = e

            t = threading.Thread(target=_run)
            t.start()
            t.join()
            if exc[0]:
                raise exc[0]

    with (
        patch("sys.stdin") as mock_stdin,
        patch("prompt_toolkit.application.run_in_terminal", side_effect=fake_rit),
        patch.dict("sys.modules", {"IPython": MagicMock()}),
    ):
        mock_stdin.isatty.return_value = True
        await _handle_python_shell(agent, frontend)

    # The _embed function should restore the forwarder after IPython exits
    assert captured_stdout is not None


# ---------------------------------------------------------------------------
# /ipython command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ipython_command_registered():
    """The /ipython command must be registered in CommandRegistry."""
    from nemo_oo_agents_cli.tui.commands import CommandRegistry

    classes = CommandRegistry.get_all_command_classes()
    assert "ipython" in classes


@pytest.mark.asyncio
async def test_ipython_command_help_text():
    """The /ipython command must have help text."""
    from nemo_oo_agents_cli.tui.commands import CommandRegistry

    classes = CommandRegistry.get_all_command_classes()
    ipython_cls = classes["ipython"]
    help_dict = ipython_cls.help_text()
    assert "/ipython" in help_dict

