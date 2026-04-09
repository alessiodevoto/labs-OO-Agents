"""Comprehensive tests for TUI commands - input/output testing.

Tests all valid/invalid commands by verifying CommandResult outputs.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nemo_oo_agents_cli.tui.commands import CommandHandler, CommandRegistry
from nemo_oo_agents_cli.tui.output import (
    ClearScreen,
    HelpOutput,
    TableOutput,
    TextOutput,
)


@pytest.fixture
def mock_frontend():
    """Create a mock frontend that captures all output."""
    frontend = AsyncMock()
    frontend.render = AsyncMock()
    frontend.get_input = AsyncMock(return_value="")
    frontend.start_thinking = AsyncMock()
    frontend.stop_thinking = AsyncMock()
    frontend.is_connected = True
    return frontend


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
def registry(mock_frontend, mock_config, mock_agent):
    """Create a command registry."""
    return CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=[Path(".cursor/skills")],
        mcp_file=Path(".mcp.json"),
    )


@pytest.fixture
def handler(registry, mock_frontend):
    """Create a command handler."""
    return CommandHandler(registry=registry, frontend=mock_frontend)


# ============================================================================
# Basic Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_help_command_output(handler):
    """Test /help command output."""
    result = await handler.handle("/help")

    assert result.success is True
    assert any(isinstance(o, HelpOutput) for o in result.outputs)


@pytest.mark.asyncio
async def test_exit_command_output(handler):
    """Test /exit command output."""
    result = await handler.handle("/exit")

    assert result.success is True
    assert result.exit is True
    assert any(isinstance(o, TextOutput) for o in result.outputs)
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Goodbye" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_clear_command_output(handler, mock_agent):
    """Test /clear command output."""
    result = await handler.handle("/clear")

    assert result.success is True
    assert any(isinstance(o, ClearScreen) for o in result.outputs)
    # event_manager.clear() must NOT be called — it would destroy the old
    # session's SQLite data.  Preservation is tested in test_clear_bug.py.
    mock_agent.event_manager.clear.assert_not_called()


@pytest.mark.asyncio
async def test_model_command_output(handler, mock_config):
    """Test /model command - shows current model."""
    result = await handler.handle("/model")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Current model:" in o.content and "test-model" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_models_command_output(handler):
    """Test /models command - lists models."""
    with patch("unifiedllm.MODELS", {"provider1/model1": None, "provider2/model2": None}):
        result = await handler.handle("/models")

        assert result.success is True
        assert any(isinstance(o, TableOutput) for o in result.outputs)


# ============================================================================
# History Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_history_command_no_args_output(handler):
    """Test /history with no args - shows error."""
    result = await handler.handle("/history")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Usage: /history <status|tags>" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_history_command_invalid_subcommand_output(handler):
    """Test /history with invalid subcommand - shows error."""
    result = await handler.handle("/history invalid")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("invalid" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_history_status_output(handler):
    """Test /history status - shows status."""
    result = await handler.handle("/history status")

    assert result.success is True
    assert any(isinstance(o, TableOutput) for o in result.outputs)


@pytest.mark.asyncio
async def test_history_tags_output(handler):
    """Test /history tags - shows tags."""
    result = await handler.handle("/history tags")

    assert result.success is True
    assert any(isinstance(o, TableOutput) for o in result.outputs)


# ============================================================================
# Sandbox Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_sandbox_command_no_args_output(handler):
    """Test /sandbox with no args - shows error."""
    result = await handler.handle("/sandbox")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Usage: /sandbox <status|enable|disable>" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_sandbox_command_invalid_subcommand_output(handler):
    """Test /sandbox with invalid subcommand - shows error."""
    result = await handler.handle("/sandbox invalid")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("invalid" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_sandbox_status_output(handler):
    """Test /sandbox status - shows status."""
    result = await handler.handle("/sandbox status")

    assert result.success is True
    assert any(isinstance(o, TableOutput) for o in result.outputs)


@pytest.mark.asyncio
async def test_sandbox_enable_output(handler, mock_agent):
    """Test /sandbox enable - enables sandbox."""
    mock_agent.bash.sandbox_available = True
    mock_agent.bash.use_sandbox = False

    result = await handler.handle("/sandbox enable")

    assert result.success is True
    assert mock_agent.bash.use_sandbox is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("enabled" in o.content.lower() for o in text_outputs)


@pytest.mark.asyncio
async def test_sandbox_enable_already_enabled_output(handler, mock_agent):
    """Test /sandbox enable when already enabled - shows message."""
    mock_agent.bash.sandbox_available = True
    mock_agent.bash.use_sandbox = True

    result = await handler.handle("/sandbox enable")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("already" in o.content.lower() for o in text_outputs)


@pytest.mark.asyncio
async def test_sandbox_enable_no_srt_output(handler, mock_agent):
    """Test /sandbox enable when SRT not available - shows error."""
    mock_agent.bash.sandbox_available = False

    result = await handler.handle("/sandbox enable")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("SRT" in o.content or "not available" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_sandbox_disable_output(handler, mock_agent):
    """Test /sandbox disable - disables sandbox."""
    mock_agent.bash.use_sandbox = True

    result = await handler.handle("/sandbox disable")

    assert result.success is True
    assert mock_agent.bash.use_sandbox is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("disabled" in o.content.lower() for o in text_outputs)


@pytest.mark.asyncio
async def test_sandbox_disable_already_disabled_output(handler, mock_agent):
    """Test /sandbox disable when already disabled - shows message."""
    mock_agent.bash.use_sandbox = False

    result = await handler.handle("/sandbox disable")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("already" in o.content.lower() for o in text_outputs)


# ============================================================================
# MCP Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_mcp_command_no_args_output(handler):
    """Test /mcp with no args - shows error."""
    mock_mcp_module = MagicMock()
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Usage: /mcp <list|connect|disconnect>" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_command_invalid_subcommand_output(handler):
    """Test /mcp with invalid subcommand - shows error."""
    mock_mcp_module = MagicMock()
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp invalid")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("invalid" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_list_output(handler):
    """Test /mcp list - lists servers."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.return_value = ["server1", "server2"]
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp list")

        assert result.success is True
        assert any(isinstance(o, TableOutput) for o in result.outputs)


@pytest.mark.asyncio
async def test_mcp_connect_no_server_output(handler):
    """Test /mcp connect with no server - shows error."""
    mock_mcp_module = MagicMock()
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp connect")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("mcp connect" in o.content.lower() for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_connect_not_found_output(handler):
    """Test /mcp connect with non-existent server - shows error."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.return_value = ["server1"]
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp connect nonexistent")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("nonexistent" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_connect_success_output(handler, mock_agent):
    """Test /mcp connect with valid server - connects."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.return_value = ["server1"]
    mock_mcp_module.MCPManager.create_from_server.return_value = MagicMock()
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp connect server1")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("server1" in o.content and "connected" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_disconnect_not_connected_output(handler, mock_agent):
    """Test /mcp disconnect with non-connected server - shows error."""
    if hasattr(mock_agent, "server1"):
        delattr(mock_agent, "server1")

    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.return_value = ["server1"]
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp disconnect server1")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("not connected" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_disconnect_success_output(handler, mock_agent):
    """Test /mcp disconnect with connected server - disconnects."""
    mock_agent.server1 = MagicMock()
    mcp_cmd = handler.registry.get_command("mcp")
    mcp_cmd._mcp_connections.add("server1")

    mock_mcp_module = MagicMock()
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp disconnect server1")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("server1" in o.content and "disconnected" in o.content for o in text_outputs)


# ============================================================================
# Skills Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_skills_command_no_args_output(handler):
    """Test /skills with no args - shows error."""
    result = await handler.handle("/skills")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Usage: /skills <list|activate|deactivate>" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_command_invalid_subcommand_output(handler):
    """Test /skills with invalid subcommand - shows error."""
    result = await handler.handle("/skills invalid")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("invalid" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_list_no_dirs_output(mock_frontend, mock_config):
    """Test /skills list with no directories - shows info."""
    registry = CommandRegistry(
        frontend=mock_frontend,
        config=MagicMock(default_model="test"),
        agent=MagicMock(),
        skills_dirs=None,
        mcp_file=None,
    )
    handler_no_dirs = CommandHandler(registry=registry, frontend=mock_frontend)

    result = await handler_no_dirs.handle("/skills list")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("No skills directories configured" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_list_empty_output(handler):
    """Test /skills list with no skills - shows info."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        mock_skill.discover.return_value = {}
        result = await handler.handle("/skills list")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("No skills found" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_activate_not_found_output(handler):
    """Test /skills activate with non-existent skill - shows error."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        mock_skill.discover.return_value = {"other-skill": MagicMock()}
        result = await handler.handle("/skills activate nonexistent")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("nonexistent" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_activate_already_active_output(handler, mock_agent):
    """Test /skills activate with already active skill - shows error."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        mock_skill.discover.return_value = {"test-skill": MagicMock()}
        mock_agent.test_skill = MagicMock()
        skills_cmd = handler.registry.get_command("skills")
        skills_cmd._active_skills.add("test-skill")
        result = await handler.handle("/skills activate test-skill")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("test-skill" in o.content and "already" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_activate_success_output(handler, mock_agent):
    """Test /skills activate with valid skill - activates."""
    with patch("nemo_oo_agents.SkillManager") as mock_skill:
        skill_obj = MagicMock()
        mock_skill.discover.return_value = {"test-skill": skill_obj}
        if hasattr(mock_agent, "test_skill"):
            delattr(mock_agent, "test_skill")
        skills_cmd = handler.registry.get_command("skills")
        skills_cmd._active_skills.discard("test-skill")

        result = await handler.handle("/skills activate test-skill")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("test-skill" in o.content and "activated" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_deactivate_not_active_output(handler, mock_agent):
    """Test /skills deactivate with non-active skill - shows error."""
    if hasattr(mock_agent, "test_skill"):
        delattr(mock_agent, "test_skill")

    result = await handler.handle("/skills deactivate test-skill")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("test-skill" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_deactivate_success_output(handler, mock_agent):
    """Test /skills deactivate with active skill - deactivates."""
    mock_agent.test_skill = MagicMock()
    skills_cmd = handler.registry.get_command("skills")
    skills_cmd._active_skills.add("test-skill")
    result = await handler.handle("/skills deactivate test-skill")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("test-skill" in o.content and "deactivated" in o.content for o in text_outputs)


# ============================================================================
# CommandHandler Error Cases - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_handler_unknown_command_output(handler):
    """Test unknown command - shows error with suggestion."""
    result = await handler.handle("/unknown")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("unknown" in o.content.lower() for o in text_outputs)
    assert any("/help" in o.content or "Type /help" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_handler_empty_command_output(handler):
    """Test empty command - returns error."""
    result = await handler.handle("/")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Empty command" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_handler_not_a_command_output(handler):
    """Test non-command input - returns error."""
    result = await handler.handle("not a command")

    assert result.success is False


# ============================================================================
# CommandHandler exception safety
# ============================================================================


@pytest.mark.asyncio
async def test_handler_command_exception_returns_error_not_raise(registry, mock_frontend):
    """A command whose execute() raises must yield an error result, not crash the REPL."""
    from nemo_oo_agents_cli.tui.commands import Command

    class BrokenCommand(Command):
        name = "broken"
        description = "always raises"
        usage = "/broken"
        required_capabilities: frozenset = frozenset()

        @classmethod
        def help_text(cls):
            return {"/broken": "always raises"}

        async def execute(self, args):
            raise RuntimeError("simulated command bug")

    registry._commands["broken"] = BrokenCommand(
        agent=registry.agent, config=registry.config, frontend=mock_frontend
    )

    handler = CommandHandler(registry=registry, frontend=mock_frontend)
    result = await handler.handle("/broken")

    # Must not raise; must return a failed CommandResult
    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any(
        "Command failed" in o.content or "simulated command bug" in o.content for o in text_outputs
    )


# ============================================================================
# TableOutput field ordering — MCPCommand, SkillsCommand, SandboxCommand
# ============================================================================


@pytest.mark.asyncio
async def test_mcp_list_tableoutput_fields(handler):
    """/mcp list produces a TableOutput with correct title, columns, and rows."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.return_value = ["srv-a", "srv-b"]
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp list")

    tables = [o for o in result.outputs if isinstance(o, TableOutput)]
    assert tables, "Expected a TableOutput from /mcp list"
    t = tables[0]
    assert isinstance(t.columns, list), "columns must be a list"
    assert isinstance(t.rows, list), "rows must be a list"
    assert t.title == "MCP Servers"
    assert t.columns == ["Server", "Connected"]
    assert len(t.rows) == 2
    assert t.rows[0][0] in ("srv-a", "srv-b")


@pytest.mark.asyncio
async def test_sandbox_status_tableoutput_fields(handler):
    """/sandbox status produces a TableOutput with correct title, columns, and rows."""
    result = await handler.handle("/sandbox status")

    tables = [o for o in result.outputs if isinstance(o, TableOutput)]
    assert tables, "Expected a TableOutput from /sandbox status"
    t = tables[0]
    assert isinstance(t.columns, list)
    assert isinstance(t.rows, list)
    assert t.title == "Sandbox Status"
    assert t.columns == ["Field", "Value"]
    # Each row is [str, str]
    for row in t.rows:
        assert len(row) == 2
        assert isinstance(row[0], str)
        assert isinstance(row[1], str)


@pytest.mark.asyncio
async def test_skills_list_tableoutput_fields(registry, mock_frontend):
    """/skills list produces a TableOutput with correct title, columns, and rows."""
    from unittest.mock import patch as _patch

    mock_skill = MagicMock()
    mock_skill.id = "my-skill"
    mock_skill.description = "does something"

    with _patch("nemo_oo_agents.SkillManager") as mock_sm:
        mock_sm.discover.return_value = {"my-skill": mock_skill}
        handler = CommandHandler(registry=registry, frontend=mock_frontend)
        result = await handler.handle("/skills list")

    tables = [o for o in result.outputs if isinstance(o, TableOutput)]
    assert tables, "Expected a TableOutput from /skills list"
    t = tables[0]
    assert isinstance(t.columns, list)
    assert isinstance(t.rows, list)
    assert t.title == "Skills"
    assert t.columns == ["ID", "Active", "Description"]
    for row in t.rows:
        assert len(row) == 3


# ============================================================================
# HistoryCommand._history_tags guard on missing event_manager
# ============================================================================


@pytest.mark.asyncio
async def test_history_tags_without_event_manager_returns_error(mock_frontend, mock_config):
    """An agent with get_summarization_status but no event_manager returns an error, not a crash."""

    class NoEventManagerAgent:
        def get_summarization_status(self):
            return {
                "active_events": 0,
                "policy": "auto",
                "has_summarizer": False,
                "max_tokens": 100000,
                "current_tokens": 0,
                "preserve_recent": 5,
                "summary_count": 0,
                "summary_tags": [],
            }

        event_manager = None  # present but falsy — hasattr returns True
        bash = None

    # Build a minimal agent without event_manager attribute entirely
    class TruelyNoEventManagerAgent:
        def get_summarization_status(self):
            return {
                "active_events": 0,
                "policy": "auto",
                "has_summarizer": False,
                "max_tokens": 100000,
                "current_tokens": 0,
                "preserve_recent": 5,
                "summary_count": 0,
                "summary_tags": [],
            }

        bash = None

    agent = TruelyNoEventManagerAgent()
    agent.event_manager_missing = True  # no .event_manager attr at all

    registry = CommandRegistry(
        config=mock_config,
        agent=agent,
        frontend=mock_frontend,
        skills_dirs=[],
        mcp_file=None,
    )
    handler = CommandHandler(registry=registry, frontend=mock_frontend)
    result = await handler.handle("/history tags")

    # Should return an error, not raise AttributeError
    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert text_outputs, "Expected a TextOutput error message"


# ============================================================================
# MCPCommand list_servers exception handling
# ============================================================================


@pytest.mark.asyncio
async def test_mcp_list_servers_exception_returns_error(handler):
    """/mcp list returns an error if MCPManager.list_servers raises, not a crash."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.side_effect = OSError("config file unreadable")
    with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp_module}):
        result = await handler.handle("/mcp list")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any(
        "config file unreadable" in o.content or "Failed to read MCP" in o.content
        for o in text_outputs
    )
