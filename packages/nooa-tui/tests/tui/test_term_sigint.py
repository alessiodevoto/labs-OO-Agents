# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test that ^C (SIGINT) exits cleanly in the term command."""

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner
from nooa_cli.commands.term import command


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
            "nooa_cli.web.pty_server.create_pty_app",
            return_value=(mock_app, mock_kill_all),
        ),
        patch("uvicorn.Server.serve", mock_serve),
    ):
        result = runner.invoke(command, ["--port", "19999"])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}. Output: {result.output}"
    )


def _invoke_term(args: list[str]):
    """Invoke the term command with the server and PTY app stubbed out."""
    runner = CliRunner()

    mock_app = MagicMock()
    mock_kill_all = MagicMock()
    mock_serve = AsyncMock(side_effect=KeyboardInterrupt())

    with (
        patch(
            "nooa_cli.web.pty_server.create_pty_app",
            return_value=(mock_app, mock_kill_all),
        ),
        patch("uvicorn.Server.serve", mock_serve),
    ):
        return runner.invoke(command, args)


def test_term_command_binds_loopback_by_default_with_token_url():
    """The web terminal defaults to 127.0.0.1 and prints the token URL."""
    result = _invoke_term(["--port", "19999"])

    assert result.exit_code == 0, result.output
    assert "Starting NeMo OO Agents web terminal at http://127.0.0.1:19999/?token=" in result.output
    # Loopback bind: no network-exposure warning
    assert "exposes the terminal to the network" not in result.output


def test_term_command_all_interfaces_supported_with_warning():
    """--host 0.0.0.0 still works (container use) but warns about exposure."""
    result = _invoke_term(["--host", "0.0.0.0", "--port", "19999"])

    assert result.exit_code == 0, result.output
    assert "Starting NeMo OO Agents web terminal at http://0.0.0.0:19999/?token=" in result.output
    assert "exposes the terminal to the network" in result.output
    assert "protected only by the session token" in result.output


def test_term_command_no_auth_prints_warning_and_plain_url():
    """--no-auth drops the token from the URL and prints a loud warning."""
    result = _invoke_term(["--no-auth", "--port", "19999"])

    assert result.exit_code == 0, result.output
    assert "Starting NeMo OO Agents web terminal at http://127.0.0.1:19999\n" in result.output
    assert "token=" not in result.output
    assert "--no-auth disables authentication" in result.output


def test_term_command_no_auth_network_warning_does_not_claim_token_protection():
    """When auth is disabled, the network warning must not imply token auth."""
    result = _invoke_term(["--host", "0.0.0.0", "--no-auth", "--port", "19999"])

    assert result.exit_code == 0, result.output
    assert "Starting NeMo OO Agents web terminal at http://0.0.0.0:19999\n" in result.output
    assert "exposes the terminal to the network" in result.output
    assert "authentication is disabled" in result.output
    assert "protected only by the session token" not in result.output
    assert "token=" not in result.output
    assert "--no-auth disables authentication" in result.output


def test_term_command_passes_token_to_app_and_rich_url():
    """The generated token reaches create_pty_app and the NEMO_OO_RICH_URL."""
    runner = CliRunner()
    mock_create = MagicMock(return_value=(MagicMock(), MagicMock()))
    mock_serve = AsyncMock(side_effect=KeyboardInterrupt())

    with (
        patch("nooa_cli.web.pty_server.create_pty_app", mock_create),
        patch("uvicorn.Server.serve", mock_serve),
    ):
        result = runner.invoke(command, ["--port", "19999"])

    assert result.exit_code == 0, result.output
    kwargs = mock_create.call_args.kwargs
    token = kwargs["auth_token"]
    assert token, "expected a generated auth token"
    assert kwargs["env_extra"]["NEMO_OO_RICH_URL"] == f"http://127.0.0.1:19999/rich?token={token}"
    assert f"?token={token}" in result.output
