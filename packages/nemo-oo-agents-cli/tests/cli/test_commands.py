# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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

from nemo_oo_agents.context_blocks.models import ContextWindowStats


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
    # session's SQLite data. Positive preservation is proved in
    # tests/cli/test_clear_preserves_sqlite.py with a real SQLiteStorageManager.
    mock_agent.event_manager.clear.assert_not_called()


@pytest.mark.asyncio
async def test_clear_command_shuts_down_queue_manager(handler, mock_agent):
    """Test that /clear cancels background jobs via queue_manager.shutdown().

    Regression test for GitLab #172 — /clear did not cancel background jobs,
    leaving orphaned tasks running after the session was reset.
    """
    mock_agent.queue_manager = MagicMock()
    mock_agent.queue_manager.shutdown = AsyncMock()

    result = await handler.handle("/clear")

    assert result.success is True
    mock_agent.queue_manager.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_command_works_without_queue_manager(handler, mock_agent):
    """Test that /clear works gracefully when no queue_manager is present.

    Agents without a queue_manager (e.g. non-TUI agents) should not break.
    """
    # Remove queue_manager attribute entirely
    del mock_agent.queue_manager

    result = await handler.handle("/clear")

    assert result.success is True
    assert any(isinstance(o, ClearScreen) for o in result.outputs)


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
    with patch(
        "nemo_oo_agents.unifiedllm.MODELS", {"provider1/model1": None, "provider2/model2": None}
    ):
        result = await handler.handle("/models")

        assert result.success is True
        assert any(isinstance(o, TableOutput) for o in result.outputs)


# ============================================================================
# Context Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_context_command_no_stats(handler, mock_agent):
    """Test /context before any generation — shows info message."""
    mock_agent.context_stats = None
    result = await handler.handle("/context")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("No context stats yet" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_context_command_with_stats(handler, mock_agent):
    """Test /context with stats available — shows formatted output."""
    mock_agent.context_stats = ContextWindowStats(
        context_blocks_tokens=8_000,
        context_blocks_count=5,
        events_tokens=4_000,
        events_count=20,
        total_tokens=12_000,
        max_context_tokens=32_000,
        max_event_tokens=20_000,
    )
    result = await handler.handle("/context")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Context usage:" in o.content for o in text_outputs)
    assert any("Context blocks:" in o.content for o in text_outputs)
    assert any("Events:" in o.content for o in text_outputs)


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
# MCP Command Tests - Input/Output
# ============================================================================


@pytest.mark.asyncio
async def test_mcp_command_no_args_output(handler):
    """Test /mcp with no args - shows error."""
    mock_mcp_module = MagicMock()
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
        result = await handler.handle("/mcp")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("Usage: /mcp <list|connect|disconnect>" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_command_invalid_subcommand_output(handler):
    """Test /mcp with invalid subcommand - shows error."""
    mock_mcp_module = MagicMock()
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
        result = await handler.handle("/mcp invalid")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("invalid" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_list_output(handler):
    """Test /mcp list - lists servers."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.return_value = ["server1", "server2"]
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
        result = await handler.handle("/mcp list")

        assert result.success is True
        assert any(isinstance(o, TableOutput) for o in result.outputs)


@pytest.mark.asyncio
async def test_mcp_connect_no_server_output(handler):
    """Test /mcp connect with no server - shows error."""
    mock_mcp_module = MagicMock()
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
        result = await handler.handle("/mcp connect")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("mcp connect" in o.content.lower() for o in text_outputs)


@pytest.mark.asyncio
async def test_mcp_connect_not_found_output(handler):
    """Test /mcp connect with non-existent server - shows error."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.return_value = ["server1"]
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
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
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
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
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
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
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
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
    assert any("Usage: /skills" in o.content for o in text_outputs)


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
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
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
# User-invocable skill commands
# ============================================================================


@pytest.fixture
def skills_dir(tmp_path):
    """Fixture mirroring the REAL skill layout on the user's machine.

    All skills live in a single directory (no commands/ split).
    User-invocable skills are identified by argument-hint without user-invocable: false.
    Skills with user-invocable: false are explicitly opted out.
    Skills without argument-hint are plain context skills (not user commands).

    wtf/          ← argument-hint, no opt-out → user command
    wtf-status/   ← argument-hint, no opt-out → user command
    wtf-issue-add/← argument-hint + user-invocable: false (invalid YAML) → NOT a command
    trace-explorer/← no argument-hint → NOT a command
    """
    # wtf — has argument-hint, no install-as, no user-invocable flag
    (tmp_path / "wtf").mkdir()
    (tmp_path / "wtf" / "SKILL.md").write_text(
        "---\n"
        "name: wtf\n"
        "description: Manage WTF issues\n"
        "argument-hint: <action> [issue-id] [details]\n"
        "allowed-tools: Bash(uv:*), Bash(.venv/bin/wtf:*), Read\n"
        "disable-model-invocation: true\n"
        "---\n\n"
        "# WTF\n\nUse this skill to manage issues.\n"
    )

    # wtf-status — argument-hint, no opt-out
    (tmp_path / "wtf-status").mkdir()
    (tmp_path / "wtf-status" / "SKILL.md").write_text(
        "---\n"
        "name: wtf-status\n"
        "description: Show WTF project status\n"
        "argument-hint: [label]\n"
        "allowed-tools: Bash(uv:*), Bash(.venv/bin/wtf:*)\n"
        "---\n\n"
        "# WTF Status\n\nShow project status.\n"
    )

    # wtf-issue-add — argument-hint + user-invocable: false + invalid YAML arg-hint
    (tmp_path / "wtf-issue-add").mkdir()
    (tmp_path / "wtf-issue-add" / "SKILL.md").write_text(
        "---\n"
        "name: wtf-issue-add\n"
        "description: Create a new WTF issue\n"
        'argument-hint: "<title>" [-p priority] [-a assignee]\n'
        "allowed-tools: Bash(uv:*), Bash(.venv/bin/wtf:*)\n"
        "user-invocable: false\n"
        "---\n\n"
        "# WTF Issue Add\n\nCreate an issue.\n"
    )

    # trace-explorer — explicitly opted out with user-invocable: false
    (tmp_path / "trace-explorer").mkdir()
    (tmp_path / "trace-explorer" / "SKILL.md").write_text(
        "---\n"
        "name: trace-explorer\n"
        "description: Explore agent execution traces\n"
        "user-invocable: false\n"
        "---\n\n"
        "# Trace Explorer\n\nExplore traces.\n"
    )

    return tmp_path


def _skills_dirs_from(tmp_path):
    """Return [tmp_path] for the skills_dir fixture layout (flat, no subdirs)."""
    return [tmp_path]


@pytest.fixture
def registry_with_skills(mock_frontend, mock_config, mock_agent, skills_dir):
    return CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=_skills_dirs_from(skills_dir),
        mcp_file=Path(".mcp.json"),
    )


@pytest.fixture
def handler_with_skills(registry_with_skills, mock_frontend):
    return CommandHandler(registry=registry_with_skills, frontend=mock_frontend)


def test_user_skill_with_argument_hint_is_discovered(registry_with_skills):
    """Skills with argument-hint and no user-invocable: false are user commands.

    This is the real-world pattern: wtf/wtf-status have argument-hint with no opt-out.
    They should appear as /wtf and /wtf-status slash commands.
    """
    skill = registry_with_skills.get_user_skill("wtf")
    assert skill is not None
    assert skill.name == "wtf"
    assert skill.description == "Manage WTF issues"
    assert skill.argument_hint == "<action> [issue-id] [details]"


def test_both_argument_hint_skills_discovered(registry_with_skills):
    """Both wtf and wtf-status (both have argument-hint, no opt-out) appear as user commands."""
    assert registry_with_skills.get_user_skill("wtf") is not None
    assert registry_with_skills.get_user_skill("wtf-status") is not None


def test_opted_out_and_plain_skills_not_discovered(registry_with_skills):
    """Skills with user-invocable: false or no argument-hint are NOT user commands."""
    assert registry_with_skills.get_user_skill("wtf-issue-add") is None  # explicit opt-out
    assert registry_with_skills.get_user_skill("trace-explorer") is None  # no argument-hint


def test_user_skill_appears_in_active_help(registry_with_skills):
    """User-invocable skill appears in /help output."""
    help_text = registry_with_skills.get_active_help()
    assert any("wtf" in key for key in help_text)


def test_user_skill_appears_in_completions(registry_with_skills):
    """User-invocable skill appears in Tab completions."""
    completions = registry_with_skills.get_completions()
    assert any("wtf" in key for key in completions)


@pytest.mark.asyncio
async def test_user_skill_invocation_sets_agent_message(handler_with_skills):
    """/wtf returns agent_message with the skill body — no outputs rendered."""
    result = await handler_with_skills.handle("/wtf")
    assert result.success is True
    assert result.agent_message is not None
    assert "WTF" in result.agent_message
    assert result.outputs == []


@pytest.mark.asyncio
async def test_user_skill_invocation_with_args_appended(handler_with_skills):
    """/wtf list appends args to the skill body."""
    result = await handler_with_skills.handle("/wtf list gl-42")
    assert result.agent_message is not None
    assert "list gl-42" in result.agent_message


@pytest.mark.asyncio
async def test_user_skill_arguments_substitution(tmp_path, mock_frontend, mock_config, mock_agent):
    """$ARGUMENTS placeholder in skill body is substituted with user args."""
    skill_dir = tmp_path / "commit"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: commit\ndescription: Commit\ninstall-as: command\n---\n"
        "Write a commit for: $ARGUMENTS\n"
    )
    registry = CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=[tmp_path],
        mcp_file=Path(".mcp.json"),
    )
    handler = CommandHandler(registry=registry, frontend=mock_frontend)
    result = await handler.handle("/commit fix the login bug")
    assert result.agent_message is not None
    assert "fix the login bug" in result.agent_message
    assert "$ARGUMENTS" not in result.agent_message


# ============================================================================
# Auto-attach skills to agent at startup
# ============================================================================


def test_all_skills_auto_attached_to_agent(skills_dir, mock_frontend, mock_config):
    """ALL skills (user-invokable AND plain) are attached to the agent when registry is created."""
    from unittest.mock import MagicMock

    from nemo_oo_agents.skill import TextSkill

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
    agent.bash = MagicMock()
    agent.event_manager = MagicMock()

    # MagicMock reports hasattr(...) = True for everything, but setattr should work
    # Use a real object so we can check attributes were set
    class _FakeAgent:
        pass

    real_agent = _FakeAgent()
    real_agent.get_summarization_status = lambda: {
        "active_events": 0,
        "policy": "auto",
        "has_summarizer": False,
        "max_tokens": 100000,
        "current_tokens": 0,
        "preserve_recent": 5,
        "summary_count": 0,
        "summary_tags": [],
    }
    real_agent.event_manager = MagicMock()
    real_agent.bash = MagicMock()
    real_agent._llm = MagicMock()

    CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=real_agent,
        skills_dirs=_skills_dirs_from(skills_dir),
        mcp_file=None,
    )

    # All skills (user-invokable AND plain) should be attached to the agent
    assert hasattr(real_agent, "wtf"), "skill 'wtf' was not attached to agent"
    assert hasattr(real_agent, "wtf_status"), "skill 'wtf-status' was not attached to agent"
    assert hasattr(real_agent, "trace_explorer"), (
        "plain skill 'trace-explorer' was not attached to agent"
    )
    assert isinstance(real_agent.wtf, TextSkill)
    assert isinstance(real_agent.trace_explorer, TextSkill)


def test_user_invokable_skill_also_attached_to_agent(skills_dir, mock_frontend, mock_config):
    """install-as:command skill is attached to agent as a TextSkill attribute."""
    from nemo_oo_agents.skill import TextSkill

    class _FakeAgent:
        pass

    agent = _FakeAgent()
    agent.get_summarization_status = lambda: {
        "active_events": 0,
        "policy": "auto",
        "has_summarizer": False,
        "max_tokens": 100000,
        "current_tokens": 0,
        "preserve_recent": 5,
        "summary_count": 0,
        "summary_tags": [],
    }
    agent.event_manager = MagicMock()
    agent.bash = MagicMock()
    agent._llm = MagicMock()

    CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=agent,
        skills_dirs=_skills_dirs_from(skills_dir),
        mcp_file=None,
    )

    assert isinstance(agent.wtf, TextSkill)
    assert agent.wtf.description == "Manage WTF issues"


def test_user_skill_discovered_when_nested(mock_frontend, mock_config, mock_agent, tmp_path):
    """install-as:command skill nested two levels deep is still discovered.

    /skills list uses SkillManager.discover() which uses rglob (recursive).
    _discover_user_skills() must also use rglob so the two stay in sync.
    Previously used iterdir() (one level only) — this test would have failed then.
    """
    # Nest the skill two levels deep: tmp_path/group/wtf/SKILL.md
    group_dir = tmp_path / "group"
    group_dir.mkdir()
    skill_dir = group_dir / "wtf"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: wtf\ndescription: Nested WTF\ninstall-as: command\n---\nBody.\n"
    )

    registry = CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=[tmp_path],
        mcp_file=None,
    )
    skill = registry.get_user_skill("wtf")
    assert skill is not None, (
        "Nested skill not discovered — _discover_user_skills must use rglob, not iterdir"
    )
    assert skill.name == "wtf"


def test_skill_with_invalid_yaml_argument_hint_is_discovered(
    mock_frontend, mock_config, mock_agent, tmp_path
):
    """A skill whose frontmatter contains an invalid-YAML argument-hint must still be discovered.

    Claude Code-style hints like '"<action>" [issue-id]' are a quoted scalar followed by
    unstructured text — invalid YAML.  yaml.safe_load() raises on the ENTIRE block, so
    naive code falls back to meta={}, loses install-as, and silently skips the skill.

    The fix: add a line-by-line regex fallback (same as nemo_oo_agents._parse_frontmatter).
    """
    skill_dir = tmp_path / "wtf"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: wtf\n"
        "description: Manage WTF issues\n"
        "install-as: command\n"
        'argument-hint: "<action>" [issue-id]\n'
        "---\n"
        "Use this skill to manage issues.\n"
    )

    registry = CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=[tmp_path],
        mcp_file=None,
    )
    skill = registry.get_user_skill("wtf")
    assert skill is not None, (
        "Skill with invalid-YAML argument-hint must still be discovered — "
        "yaml.safe_load() fails on the whole block, not just that field"
    )
    assert skill.name == "wtf"
    assert skill.description == "Manage WTF issues"
    assert skill.argument_hint == '"<action>" [issue-id]'


def test_skill_with_user_invocable_false_and_invalid_yaml_not_discovered(
    mock_frontend, mock_config, mock_agent, tmp_path
):
    """user-invocable: false must suppress discovery even when YAML parsing falls back to regex.

    Root cause: if argument-hint (or any field) contains invalid YAML, yaml.safe_load()
    fails on the ENTIRE block and we fall back to a line-by-line regex.  The regex stores
    raw strings, so user-invocable becomes the string "false" — which is TRUTHY in Python.
    `not "false"` is False, so the filter passes and the skill is incorrectly registered
    as a user command.

    Fix: parse each individual scalar value with yaml.safe_load() so that "false" → False.
    """
    skill_dir = tmp_path / "wtf-issue-add"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: wtf-issue-add\n"
        "description: Create a new WTF issue\n"
        "user-invocable: false\n"
        'argument-hint: "<title>" [-p priority]\n'  # invalid YAML → triggers regex fallback
        "---\n"
        "Body.\n"
    )

    registry = CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=[tmp_path],
        mcp_file=None,
    )
    skill = registry.get_user_skill("wtf-issue-add")
    assert skill is None, (
        "Skill with user-invocable: false must NOT be registered as a user command — "
        "the regex fallback stores 'false' as a string which is truthy"
    )


def test_skills_not_attached_when_no_dirs(mock_frontend, mock_config, mock_agent):
    """Registry with no skills_dirs does not attempt to attach skills."""
    registry = CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=None,
        mcp_file=None,
    )
    # No error raised — auto-install is a no-op
    assert registry._user_skills == {}


# ============================================================================
# MCPCommand list_servers exception handling
# ============================================================================


@pytest.mark.asyncio
async def test_mcp_list_servers_exception_returns_error(handler):
    """/mcp list returns an error if MCPManager.list_servers raises, not a crash."""
    mock_mcp_module = MagicMock()
    mock_mcp_module.MCPManager.list_servers.side_effect = OSError("config file unreadable")
    with patch.dict("sys.modules", {"nemo_oo_agents.mcp": mock_mcp_module}):
        result = await handler.handle("/mcp list")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any(
        "config file unreadable" in o.content or "Failed to read MCP" in o.content
        for o in text_outputs
    )
