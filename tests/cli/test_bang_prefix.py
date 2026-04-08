"""Tests for ! prefix direct bash execution in the REPL."""

from unittest.mock import AsyncMock

import pytest

from nemo_oo_agents.tools.bash_tool import BashResult


@pytest.mark.asyncio
async def test_bang_prefix_runs_bash_directly():
    """! prefix should run command through bash, not the agent."""
    mock_bash = AsyncMock()
    mock_bash.run.return_value = BashResult(stdout="hello\n", stderr="", return_code=0)

    from nemo_oo_agents_cli.tui.main import handle_bang_command

    result = await handle_bang_command("!echo hello", mock_bash)

    mock_bash.run.assert_called_once_with("echo hello")
    assert result.stdout == "hello\n"


@pytest.mark.asyncio
async def test_bang_prefix_strips_whitespace():
    """Leading/trailing whitespace after ! should be stripped before running."""
    mock_bash = AsyncMock()
    mock_bash.run.return_value = BashResult(stdout="", stderr="", return_code=0)

    from nemo_oo_agents_cli.tui.main import handle_bang_command

    await handle_bang_command("!  git status  ", mock_bash)

    mock_bash.run.assert_called_once_with("git status")


@pytest.mark.asyncio
async def test_bang_prefix_empty_command_returns_none():
    """! with no command should return None without calling bash."""
    mock_bash = AsyncMock()

    from nemo_oo_agents_cli.tui.main import handle_bang_command

    result = await handle_bang_command("!", mock_bash)

    assert result is None
    mock_bash.run.assert_not_called()


@pytest.mark.asyncio
async def test_bang_prefix_whitespace_only_returns_none():
    """! followed by only whitespace should return None."""
    mock_bash = AsyncMock()

    from nemo_oo_agents_cli.tui.main import handle_bang_command

    result = await handle_bang_command("!   ", mock_bash)

    assert result is None
    mock_bash.run.assert_not_called()
