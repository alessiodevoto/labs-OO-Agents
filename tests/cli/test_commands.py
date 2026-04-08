"""Comprehensive tests for TUI commands - input/output testing.

Tests all valid/invalid commands by verifying console output messages.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nemo_oo_agents_cli.tui.commands import CommandHandler, CommandRegistry


@pytest.fixture
def mock_console():
    """Create a mock TUI console that captures all output."""
    console = MagicMock()
    console.print_help = MagicMock()
    console.print_success = MagicMock()
    console.print_error = MagicMock()
    console.print_warning = MagicMock()
    console.print_info = MagicMock()
    console.print_status = MagicMock()
    console.print_agent = MagicMock()
    console.print_table = MagicMock()
    console.console = MagicMock()
    console.console.print = MagicMock()
    console.console.clear = MagicMock()
    console.start_spinner = MagicMock()
    console.stop_spinner = MagicMock()
    console.thinking_spinner = MagicMock()
    return console


@pytest.fixture
def mock_config():
    """Create a mock TUI config."""
    config = MagicMock()
    config.default_model = "test-model"
    return config


@pytest.fixture
def mock_agent(mock_config):
    """Create a mock TUI agent."""
    agent = MagicMock()
    agent._llm = MagicMock()
    agent.event_manager = MagicMock()
    agent.event_manager.clear = MagicMock()
    agent.event_manager.keys = MagicMock(return_value=["tag1", "tag2"])
    agent.get_summarization_status = MagicMock(
        return_value={
            "active_events": 10,
            "policy": "auto",
            "has_summarizer": True,
            "max_tokens": 100000,
            "current_tokens": 50000,
            "preserve_recent": 5,
            "summary_count": 2,
            "summary_tags": ["summary1", "summary2"],
        }
    )
    agent.bash = MagicMock()
    agent.bash.use_sandbox = False
    agent.bash.sandbox_available = True
    agent.bash.run = AsyncMock(
        return_value=MagicMock(stdout="test output", stderr="", return_code=0, sandboxed=True)
    )
    return agent


@pytest.fixture
def registry(mock_console, mock_config, mock_agent):
    """Create a command registry."""
    return CommandRegistry(
        console=mock_console,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=[Path(".cursor/skills")],
        mcp_file=Path(".mcp.json"),
    )


@pytest.fixture
def handler(registry, mock_console):
    """Create a command handler."""
    return CommandHandler(registry=registry, console=mock_console)


# ============================================================================
# Helper to get printed messages
# ============================================================================


def get_printed_messages(console, method_name: str) -> list[str]:
    """Get all messages printed via a console method."""
    method = getattr(console, method_name)
    return [call.args[0] if call.args else "" for call in method.call_args_list]


# ============================================================================
# Basic Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_help_command_output(handler, mock_console):
    """Test /help command output."""
    result = await handler.handle("/help")

    assert result.success is True
    mock_console.print_help.assert_called_once()


@pytest.mark.asyncio
async def test_exit_command_output(handler, mock_console):
    """Test /exit command output."""
    result = await handler.handle("/exit")

    assert result.success is True
    assert result.exit is True
    mock_console.print_status.assert_called_once()
    assert "Goodbye! Stay vibing." in mock_console.print_status.call_args[0][0]


@pytest.mark.asyncio
async def test_clear_command_output(handler, mock_console, mock_agent):
    """Test /clear command output."""
    result = await handler.handle("/clear")

    assert result.success is True
    mock_console.console.clear.assert_called_once()


@pytest.mark.asyncio
async def test_model_command_output(handler, mock_console, mock_config):
    """Test /model command - shows current model."""
    result = await handler.handle("/model")

    assert result.success is True
    mock_console.print_info.assert_called_once()
    # Check that the message contains the model name (ignoring rich formatting)
    call_args = mock_console.print_info.call_args[0][0]
    assert "Current model:" in call_args
    assert "test-model" in call_args


@pytest.mark.asyncio
async def test_models_command_output(handler, mock_console):
    """Test /models command - lists models."""
    with patch("unifiedllm.MODELS", {"provider1/model1": None, "provider2/model2": None}):
        result = await handler.handle("/models")

        assert result.success is True
        # Should print models
        assert mock_console.console.print.call_count > 0


# ============================================================================
# History Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_history_command_no_args_output(handler, mock_console):
    """Test /history with no args - shows error."""
    result = await handler.handle("/history")

    assert result.success is False
    assert "Usage: /history <status|tags>" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_history_command_invalid_subcommand_output(handler, mock_console):
    """Test /history with invalid subcommand - shows error."""
    result = await handler.handle("/history invalid")

    assert result.success is False
    assert "Unknown subcommand: `invalid`" in result.message
    assert "Usage: /history <status|tags>" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_history_status_output(handler, mock_console):
    """Test /history status - shows status."""
    result = await handler.handle("/history status")

    assert result.success is True
    # Should print status information
    assert mock_console.console.print.call_count > 0


@pytest.mark.asyncio
async def test_history_tags_output(handler, mock_console):
    """Test /history tags - shows tags."""
    result = await handler.handle("/history tags")

    assert result.success is True
    # Should print tags
    assert mock_console.console.print.call_count > 0


# ============================================================================
# Sandbox Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_sandbox_command_no_args_output(handler, mock_console):
    """Test /sandbox with no args - shows error."""
    result = await handler.handle("/sandbox")

    assert result.success is False
    assert "Usage: /sandbox <status|enable|disable>" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_sandbox_command_invalid_subcommand_output(handler, mock_console):
    """Test /sandbox with invalid subcommand - shows error."""
    result = await handler.handle("/sandbox invalid")

    assert result.success is False
    assert "Unknown subcommand: `invalid`" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_sandbox_status_output(handler, mock_console):
    """Test /sandbox status - shows status."""
    result = await handler.handle("/sandbox status")

    assert result.success is True
    # Should print status
    assert mock_console.console.print.call_count > 0


@pytest.mark.asyncio
async def test_sandbox_enable_output(handler, mock_console, mock_agent):
    """Test /sandbox enable - enables sandbox."""
    mock_agent.bash.sandbox_available = True
    mock_agent.bash.use_sandbox = False

    result = await handler.handle("/sandbox enable")

    assert result.success is True
    assert "Sandbox enabled for bash commands" in result.message
    mock_console.print_success.assert_called_once()
    assert "Sandbox enabled for bash commands" in mock_console.print_success.call_args[0][0]
    assert mock_agent.bash.use_sandbox is True


@pytest.mark.asyncio
async def test_sandbox_enable_already_enabled_output(handler, mock_console, mock_agent):
    """Test /sandbox enable when already enabled - shows message."""
    mock_agent.bash.sandbox_available = True
    mock_agent.bash.use_sandbox = True

    result = await handler.handle("/sandbox enable")

    assert result.success is True
    assert "Sandbox already enabled" in result.message
    mock_console.print_success.assert_called_once()


@pytest.mark.asyncio
async def test_sandbox_enable_no_srt_output(handler, mock_console, mock_agent):
    """Test /sandbox enable when SRT not available - shows error."""
    mock_agent.bash.sandbox_available = False

    result = await handler.handle("/sandbox enable")

    assert result.success is False
    assert "SRT sandbox not available" in result.message
    mock_console.print_error.assert_called_once()
    assert "SRT sandbox not available" in mock_console.print_error.call_args[0][0]


@pytest.mark.asyncio
async def test_sandbox_disable_output(handler, mock_console, mock_agent):
    """Test /sandbox disable - disables sandbox."""
    mock_agent.bash.use_sandbox = True

    result = await handler.handle("/sandbox disable")

    assert result.success is True
    assert "Sandbox disabled for bash commands" in result.message
    mock_console.print_success.assert_called_once()
    assert "Sandbox disabled for bash commands" in mock_console.print_success.call_args[0][0]
    assert mock_agent.bash.use_sandbox is False


@pytest.mark.asyncio
async def test_sandbox_disable_already_disabled_output(handler, mock_console, mock_agent):
    """Test /sandbox disable when already disabled - shows error."""
    mock_agent.bash.use_sandbox = False

    result = await handler.handle("/sandbox disable")

    assert result.success is False
    assert "Sandbox is already disabled" in result.message
    mock_console.print_error.assert_called_once()


# ============================================================================
# MCP Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_mcp_command_no_args_output(handler, mock_console):
    """Test /mcp with no args - shows error."""
    result = await handler.handle("/mcp")

    assert result.success is False
    assert "Usage: /mcp <list|connect|disconnect>" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_command_invalid_subcommand_output(handler, mock_console):
    """Test /mcp with invalid subcommand - shows error."""
    result = await handler.handle("/mcp invalid")

    assert result.success is False
    assert "Unknown subcommand: `invalid`" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_list_output(handler, mock_console):
    """Test /mcp list - lists servers."""
    with patch("mcp_nemo_oo_agents.MCPManager") as mock_mcp:
        mock_mcp.list_servers.return_value = ["server1", "server2"]
        result = await handler.handle("/mcp list")

        assert result.success is True
        mock_console.print_table.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_connect_no_server_output(handler, mock_console):
    """Test /mcp connect with no server - shows error."""
    result = await handler.handle("/mcp connect")

    assert result.success is False
    assert "Usage: /mcp connect <server_name>" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_connect_not_found_output(handler, mock_console):
    """Test /mcp connect with non-existent server - shows error."""
    with patch("mcp_nemo_oo_agents.MCPManager") as mock_mcp:
        mock_mcp.list_servers.return_value = ["server1"]
        result = await handler.handle("/mcp connect nonexistent")

        assert result.success is False
        assert "MCP server `nonexistent` not found" in result.message
        mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_connect_success_output(handler, mock_console, mock_agent):
    """Test /mcp connect with valid server - connects."""
    with patch("mcp_nemo_oo_agents.MCPManager") as mock_mcp:
        mock_mcp.list_servers.return_value = ["server1"]
        mock_mcp.create_from_server.return_value = MagicMock()
        result = await handler.handle("/mcp connect server1")

        assert result.success is True
        assert "MCP server `server1` connected" in result.message
        mock_console.print_success.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_disconnect_not_connected_output(handler, mock_console, mock_agent):
    """Test /mcp disconnect with non-connected server - shows error."""
    # Ensure server1 is not connected
    if hasattr(mock_agent, "server1"):
        delattr(mock_agent, "server1")

    result = await handler.handle("/mcp disconnect server1")

    assert result.success is False
    assert "MCP server `server1` not connected" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_disconnect_success_output(handler, mock_console, mock_agent):
    """Test /mcp disconnect with connected server - disconnects."""
    # Simulate a connected server by adding it to the command's tracking set
    # and setting the agent attribute
    mock_agent.server1 = MagicMock()
    # Get the MCP command instance and add server1 to its connections
    mcp_cmd = handler.registry.get_command("mcp")
    mcp_cmd._mcp_connections.add("server1")
    result = await handler.handle("/mcp disconnect server1")

    assert result.success is True
    assert "MCP server `server1` disconnected" in result.message
    mock_console.print_success.assert_called_once()


# ============================================================================
# Skills Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_skills_command_no_args_output(handler, mock_console):
    """Test /skills with no args - shows error."""
    result = await handler.handle("/skills")

    assert result.success is False
    assert "Usage: /skills <list|activate|deactivate>" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_skills_command_invalid_subcommand_output(handler, mock_console):
    """Test /skills with invalid subcommand - shows error."""
    result = await handler.handle("/skills invalid")

    assert result.success is False
    assert "Unknown subcommand: `invalid`" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_skills_list_no_dirs_output(handler, mock_console):
    """Test /skills list with no directories - shows info."""
    # Create handler with no skills dirs
    from nemo_oo_agents_cli.tui.commands import CommandHandler, CommandRegistry

    registry = CommandRegistry(
        console=mock_console,
        config=MagicMock(default_model="test"),
        agent=MagicMock(),
        skills_dirs=None,
        mcp_file=None,
    )
    handler_no_dirs = CommandHandler(registry=registry, console=mock_console)

    result = await handler_no_dirs.handle("/skills list")

    assert result.success is True
    info_calls = [call.args[0] for call in mock_console.print_info.call_args_list]
    assert any("No skills directories configured" in msg for msg in info_calls)


@pytest.mark.asyncio
async def test_skills_list_empty_output(handler, mock_console):
    """Test /skills list with no skills - shows info."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        mock_skill.discover.return_value = {}
        result = await handler.handle("/skills list")

        assert result.success is True
        info_calls = [call.args[0] for call in mock_console.print_info.call_args_list]
        assert any("No skills found" in msg for msg in info_calls)


@pytest.mark.asyncio
async def test_skills_activate_not_found_output(handler, mock_console):
    """Test /skills activate with non-existent skill - shows error."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        # Mock discover to return different skill
        mock_skill.discover.return_value = {"other-skill": MagicMock()}
        result = await handler.handle("/skills activate nonexistent")

        assert result.success is False
        assert "Skill `nonexistent` not found" in result.message
        # Verify error was printed to console
        mock_console.print_error.assert_called_once()
        assert "Skill `nonexistent` not found" in mock_console.print_error.call_args[0][0]


@pytest.mark.asyncio
async def test_skills_activate_already_active_output(handler, mock_console, mock_agent):
    """Test /skills activate with already active skill - shows error."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        mock_skill.discover.return_value = {"test-skill": MagicMock()}
        mock_agent.test_skill = MagicMock()
        # Get the skills command instance and add test-skill to its active set
        skills_cmd = handler.registry.get_command("skills")
        skills_cmd._active_skills.add("test-skill")
        result = await handler.handle("/skills activate test-skill")

        assert result.success is False
        assert "Skill `test-skill` is already activated" in result.message
        mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_skills_activate_success_output(handler, mock_console, mock_agent):
    """Test /skills activate with valid skill - activates."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        skill_obj = MagicMock()
        # Mock discover to return the skill
        mock_skill.discover.return_value = {"test-skill": skill_obj}
        # Ensure skill is not already active
        if hasattr(mock_agent, "test_skill"):
            delattr(mock_agent, "test_skill")
        # Ensure skill is not in active set
        skills_cmd = handler.registry.get_command("skills")
        skills_cmd._active_skills.discard("test-skill")

        result = await handler.handle("/skills activate test-skill")

        assert result.success is True
        assert "Skill `test-skill` activated" in result.message
        mock_console.print_success.assert_called_once()
        assert "Skill `test-skill` activated" in mock_console.print_success.call_args[0][0]


@pytest.mark.asyncio
async def test_skills_deactivate_not_active_output(handler, mock_console, mock_agent):
    """Test /skills deactivate with non-active skill - shows error."""
    # Ensure skill is not active
    if hasattr(mock_agent, "test_skill"):
        delattr(mock_agent, "test_skill")

    result = await handler.handle("/skills deactivate test-skill")

    assert result.success is False
    assert "Skill `test-skill` not active" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_skills_deactivate_success_output(handler, mock_console, mock_agent):
    """Test /skills deactivate with active skill - deactivates."""
    mock_agent.test_skill = MagicMock()
    # Get the skills command instance and add test-skill to its active set
    skills_cmd = handler.registry.get_command("skills")
    skills_cmd._active_skills.add("test-skill")
    result = await handler.handle("/skills deactivate test-skill")

    assert result.success is True
    assert "Skill `test-skill` deactivated" in result.message
    mock_console.print_success.assert_called_once()
    assert "Skill `test-skill` deactivated" in mock_console.print_success.call_args[0][0]


# ============================================================================
# CommandHandler Error Cases - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_handler_unknown_command_output(handler, mock_console):
    """Test unknown command - shows error with suggestion."""
    result = await handler.handle("/unknown")

    assert result.success is False
    assert "Unknown command: /unknown" in result.message
    assert "Type /help" in result.message
    mock_console.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_handler_empty_command_output(handler):
    """Test empty command - returns error."""
    result = await handler.handle("/")

    assert result.success is False
    assert "Empty command" in result.message


@pytest.mark.asyncio
async def test_handler_not_a_command_output(handler):
    """Test non-command input - returns error."""
    result = await handler.handle("not a command")

    assert result.success is False
    assert "Not a command" in result.message
