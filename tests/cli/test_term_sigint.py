"""Test that ^C (SIGINT) exits cleanly in the term command."""

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from nemo_oo_agents_cli.commands.term import command


def test_term_command_handles_sigint_cleanly():
    """KeyboardInterrupt during Server.serve() must exit with code 0.

    The ``term`` command uses ``uvicorn.Server.serve()`` (not the removed
    ``uvicorn.run()`` helper), so we patch at that level.  We also stub out
    ``create_pty_app`` to avoid real PTY / filesystem side-effects.
    """
    runner = CliRunner()

    mock_app = MagicMock()
    mock_kill_all = MagicMock()
    mock_serve = AsyncMock(side_effect=KeyboardInterrupt())

    with (
        patch(
            "nemo_oo_agents_cli.web.pty_server.create_pty_app",
            return_value=(mock_app, mock_kill_all),
        ),
        patch("uvicorn.Server.serve", mock_serve),
    ):
        result = runner.invoke(command, ["--port", "19999"])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}. Output: {result.output}"
    )
