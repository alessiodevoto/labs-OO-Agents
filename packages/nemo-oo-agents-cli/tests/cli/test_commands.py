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
from nemo_oo_agents.skill_registry import SkillRegistry


def _attach_registry(agent):
    """Attach a SkillRegistry (with no entry points) to a fake agent for testing."""
    with patch("nemo_oo_agents.skill_registry.entry_points", return_value=[]):
        agent.skills = SkillRegistry(agent)
    return agent.skills


def _attach_mcp_registry(agent, config):
    """Attach a real MCPRegistry to a fake agent, mirroring bootstrap wiring."""
    from nemo_oo_agents_cli.tui.mcp_registry import MCPRegistry

    servers = getattr(config, "mcp_servers", None)
    if not isinstance(servers, dict):
        servers = {}
    registry = MCPRegistry(mcp_file=Path(".mcp.json"), servers=dict(servers))
    registry.attach(agent)
    agent.mcp = registry
    return registry


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
    _attach_registry(agent)
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
    _attach_mcp_registry(agent, mock_config)
    return agent


@pytest.fixture
def registry(mock_frontend, mock_config, mock_agent):
    """Create a command registry."""
    return CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=[Path("/nonexistent/skills")],
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
async def test_clear_command_defers_queue_manager_shutdown_to_session(handler, mock_agent):
    """Command parsing must not shutdown QueueManager from the UI loop.

    Session._run_command/_swap_session_manager owns acknowledged cancellation
    and loop-affine QueueManager shutdown once the new session manager is ready.
    """
    mock_agent.queue_manager = MagicMock()
    mock_agent.queue_manager.shutdown = AsyncMock()

    result = await handler.handle("/clear")

    assert result.success is True
    mock_agent.queue_manager.shutdown.assert_not_called()


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
# MCP slash command (skill-owned) + CommandRegistry auto-connect
# ============================================================================


def _mcp_registry_skill(servers=None):
    """A real MCPRegistry attached to a throwaway agent for slash-command tests."""
    from nemo_oo_agents_cli.tui.mcp_registry import MCPRegistry

    from nemo_oo_agents.runtime.context_manager import ContextManager

    class _Agent:
        def __init__(self):
            self.context_manager = ContextManager()

    reg = MCPRegistry(servers=dict(servers or {}))
    reg.attach(_Agent())
    return reg


@pytest.mark.asyncio
async def test_mcp_slash_list_returns_status():
    """/mcp list returns the status text; the command is flagged output_to_agent=False."""
    from nemo_oo_agents.skill import get_slash_commands

    reg = _mcp_registry_skill({"maas": {"url": "https://x/mcp"}})
    out = await reg.mcp_command("list")
    assert isinstance(out, str)
    assert "maas" in out
    meta = next(m for m, _ in get_slash_commands(reg) if m.name == "mcp")
    assert meta.output_to_agent is False


@pytest.mark.asyncio
async def test_mcp_slash_connect_unknown_server():
    reg = _mcp_registry_skill({"maas": {"url": "https://x/mcp"}})
    out = await reg.mcp_command("connect", "missing")
    assert "not found" in out


@pytest.mark.asyncio
async def test_mcp_slash_connect_delegates(monkeypatch):
    reg = _mcp_registry_skill({"maas": {"url": "https://x/mcp"}})

    async def _connect(patterns, **kwargs):
        reg._connected["maas"] = object()
        reg._activated.add("maas")
        return list(patterns)

    monkeypatch.setattr(reg, "connect", _connect)
    out = await reg.mcp_command("connect", "maas")
    assert "Connected 'maas'" in out


@pytest.mark.asyncio
async def test_mcp_slash_disconnect_not_connected():
    reg = _mcp_registry_skill({"maas": {"url": "https://x/mcp"}})
    out = await reg.mcp_command("disconnect", "maas")
    assert "not connected" in out


@pytest.mark.asyncio
async def test_mcp_slash_command_is_registered_on_skill():
    from nemo_oo_agents.skill import get_slash_commands

    reg = _mcp_registry_skill()
    names = [meta.name for meta, _ in get_slash_commands(reg)]
    assert "mcp" in names


def test_command_registry_auto_connects_configured_mcp(mock_frontend, mock_config, mock_agent):
    """Configured MCP servers auto-connect via self.mcp.connect() at startup."""
    servers = {"maas": {"url": "https://maas.example/mcp", "transport": "streamable-http"}}
    mock_config.mcp_servers = servers
    mock_config.mcp_auto_connect = ["maas"]
    _attach_mcp_registry(mock_agent, mock_config)

    connected: list[str] = []

    async def _fake_connect(patterns, **kwargs):
        connected.extend(patterns)
        return list(patterns)

    with (
        patch.object(mock_agent.mcp, "discovered", return_value=["maas"]),
        patch.object(mock_agent.mcp, "connect", side_effect=_fake_connect) as conn,
    ):
        CommandRegistry(
            frontend=mock_frontend,
            config=mock_config,
            agent=mock_agent,
            skills_dirs=[Path("/nonexistent/skills")],
            mcp_file=Path(".mcp.json"),
        )

    assert connected == ["maas"]
    conn.assert_called_once_with(["maas"])


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
    agent = MagicMock()
    _attach_registry(agent)
    registry = CommandRegistry(
        frontend=mock_frontend,
        config=MagicMock(default_model="test"),
        agent=agent,
        skills_dirs=None,
        mcp_file=None,
    )
    handler_no_dirs = CommandHandler(registry=registry, frontend=mock_frontend)

    result = await handler_no_dirs.handle("/skills list")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("No skills found" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_list_empty_output(mock_frontend, mock_config):
    """Test /skills list with no skills - shows info."""
    agent = MagicMock()
    _attach_registry(agent)
    registry = CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=agent,
        skills_dirs=None,
        mcp_file=None,
    )
    handler = CommandHandler(registry=registry, frontend=mock_frontend)
    result = await handler.handle("/skills list")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("No skills found" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_activate_not_found_output(handler):
    """Test /skills activate with non-existent skill - shows error."""
    result = await handler.handle("/skills activate nonexistent")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("nonexistent" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_activate_already_active_output(handler, mock_agent):
    """Test /skills activate with already active skill - shows error."""
    from nemo_oo_agents.skill import Skill

    class _TestSkill(Skill):
        pass

    skill = _TestSkill()
    mock_agent.skills.register("test.skill", skill)
    mock_agent.skills.activate(["test.skill"])
    result = await handler.handle("/skills activate test.skill")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("test.skill" in o.content and "already" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_activate_success_output(handler, mock_agent):
    """Test /skills activate with valid skill - activates."""
    from nemo_oo_agents.skill import Skill

    class _TestSkill(Skill):
        pass

    # Register but don't activate
    mock_agent.skills.register("test.skill", _TestSkill())
    result = await handler.handle("/skills activate test.skill")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("test.skill" in o.content and "activated" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_deactivate_not_active_output(handler, mock_agent):
    """Test /skills deactivate with non-active skill - shows error."""
    result = await handler.handle("/skills deactivate test.skill")

    assert result.success is False
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("test.skill" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_skills_deactivate_success_output(handler, mock_agent):
    """Test /skills deactivate with active skill - deactivates."""
    from nemo_oo_agents.skill import Skill

    class _TestSkill(Skill):
        pass

    mock_agent.skills.register("test.skill", _TestSkill())
    mock_agent.skills.activate(["test.skill"])
    result = await handler.handle("/skills deactivate test.skill")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("test.skill" in o.content and "deactivated" in o.content for o in text_outputs)


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
# TableOutput field ordering — SkillsCommand, SandboxCommand
# ============================================================================


@pytest.mark.asyncio
async def test_skills_list_tableoutput_fields(registry, mock_frontend, mock_agent):
    """/skills list produces a TableOutput with correct title, columns, and rows."""
    from nemo_oo_agents.skill import Skill

    class _MySkill(Skill):
        pass

    mock_agent.skills.register("test.myskill", _MySkill())
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
    _attach_registry(real_agent)

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
    _attach_registry(agent)

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
# ============================================================================
# Session Command Tests
# ============================================================================


@pytest.mark.asyncio
async def test_session_list_excludes_empty_sessions(handler):
    """/session list excludes sessions with turn_count == 0."""
    from nemo_oo_agents_cli.tui.session_manager import SessionMeta

    empty_session = SessionMeta(
        id="aaaa0000-0000-0000-0000-000000000001",
        model="test-model",
        agent="TUIAgent",
        started_at=1000.0,
        last_active=1000.0,
        turn_count=0,
        name="empty",
    )
    active_session = SessionMeta(
        id="bbbb0000-0000-0000-0000-000000000002",
        model="test-model",
        agent="TUIAgent",
        started_at=2000.0,
        last_active=2000.0,
        turn_count=3,
        name="active",
    )

    with patch(
        "nemo_oo_agents_cli.tui.commands.SessionManager.list_sessions",
        return_value=[active_session, empty_session],
    ):
        result = await handler.handle("/session list")

    assert result.success is True
    table_outputs = [o for o in result.outputs if isinstance(o, TableOutput)]
    assert len(table_outputs) == 1
    # Only the active session should be in rows
    rows = table_outputs[0].rows
    assert len(rows) == 1
    assert rows[0][0] == "bbbb0000"  # short id of active session


@pytest.mark.asyncio
async def test_session_list_shows_no_sessions_when_all_empty(handler):
    """/session list shows 'No sessions found' when all sessions are empty."""
    from nemo_oo_agents_cli.tui.session_manager import SessionMeta

    empty_session = SessionMeta(
        id="aaaa0000-0000-0000-0000-000000000001",
        model="test-model",
        agent="TUIAgent",
        started_at=1000.0,
        last_active=1000.0,
        turn_count=0,
    )

    with patch(
        "nemo_oo_agents_cli.tui.commands.SessionManager.list_sessions",
        return_value=[empty_session],
    ):
        result = await handler.handle("/session list")

    assert result.success is True
    text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
    assert any("No sessions found" in o.content for o in text_outputs)


@pytest.mark.asyncio
async def test_user_skill_slash_command_case_insensitive(handler_with_skills):
    """User-invocable skill slash commands should match case-insensitively."""
    result = await handler_with_skills.handle("/WTF status")
    assert result.success is True
    assert result.agent_message is not None
    assert "Arguments: status" in result.agent_message


# ============================================================================
# refresh_skill_commands() — stale @slash_command deregistration
# ============================================================================


def test_refresh_skill_commands_removes_stale_entry(registry):
    """A removed @slash_command no longer appears after refresh.

    Reproduces the bug where reloading a skill that dropped a
    @slash_command left the old command dispatching to a dead method.
    """
    from nemo_oo_agents_cli.tui.commands import _UserSkill

    # Simulate a skill-method command registered from a previous load.
    registry._user_skills["foo"] = _UserSkill(
        name="foo",
        body="",
        description="legacy command",
        _method=lambda: None,
    )
    assert registry.get_user_skill("foo") is not None

    # Reloaded skill no longer exposes /foo.
    registry._discover_skill_commands = lambda: {}
    registry.refresh_skill_commands()

    assert registry.get_user_skill("foo") is None


def test_refresh_skill_commands_preserves_text_skill(registry):
    """SKILL.md entries (_method=None) survive a refresh."""
    from nemo_oo_agents_cli.tui.commands import _UserSkill

    registry._user_skills["bar"] = _UserSkill(
        name="bar",
        body="text-skill body",
        description="from SKILL.md",
        _method=None,
    )

    registry._discover_skill_commands = lambda: {}
    registry.refresh_skill_commands()

    skill = registry.get_user_skill("bar")
    assert skill is not None
    assert skill.body == "text-skill body"


def test_refresh_skill_commands_updates_to_fresh_set(registry):
    """Refresh replaces stale skill-method commands with the freshly discovered set."""
    from nemo_oo_agents_cli.tui.commands import _UserSkill

    # Old command that should be dropped.
    registry._user_skills["old"] = _UserSkill(
        name="old", body="", description="", _method=lambda: None
    )

    # Discovery now returns a different command.
    new_cmd = _UserSkill(name="new", body="", description="fresh", _method=lambda: None)
    registry._discover_skill_commands = lambda: {"new": new_cmd}
    registry.refresh_skill_commands()

    assert registry.get_user_skill("old") is None
    assert registry.get_user_skill("new") is not None


def test_refresh_skill_commands_updates_output_to_agent_metadata(registry):
    """Hot reload must replace stale slash metadata, not just method refs."""
    from nemo_oo_agents_cli.tui.commands import _UserSkill

    registry._user_skills["mesh-list"] = _UserSkill(
        name="mesh-list",
        body="",
        description="stale command",
        output_to_agent=True,
        _method=lambda: "old",
    )

    fresh_cmd = _UserSkill(
        name="mesh-list",
        body="",
        description="fresh command",
        output_to_agent=False,
        _method=lambda: "new",
    )
    registry._discover_skill_commands = lambda: {"mesh-list": fresh_cmd}

    registry.refresh_skill_commands()

    skill = registry.get_user_skill("mesh-list")
    assert skill is not None
    assert skill.description == "fresh command"
    assert skill.output_to_agent is False
    assert skill._method() == "new"


@pytest.mark.asyncio
async def test_session_rename_without_name_regenerates_predict_title(handler, mock_agent):
    """/session rename with no args reruns the TUI name_session Predict call."""
    from types import SimpleNamespace

    session_manager = MagicMock()
    session_manager.turns = [
        SimpleNamespace(role="user", content="Help debug a failing GitLab pipeline")
    ]
    handler.registry.get_command("session").session_manager = session_manager
    mock_agent.name_session = AsyncMock(return_value='"CI pipeline debug"')

    result = await handler.handle("/session rename")

    assert result.success is True
    mock_agent.name_session.assert_awaited_once_with("Help debug a failing GitLab pipeline")
    session_manager.rename.assert_called_once_with("CI pipeline debug", user_named=False)
    assert any(
        isinstance(o, TextOutput) and "Session renamed to: CI pipeline debug" in o.content
        for o in result.outputs
    )


@pytest.mark.asyncio
async def test_session_rename_without_name_requires_user_turn(handler):
    """Regenerating a title needs a prior user message to summarize."""
    session_manager = MagicMock()
    session_manager.turns = []
    handler.registry.get_command("session").session_manager = session_manager

    result = await handler.handle("/session rename")

    assert result.success is False
    assert any(
        isinstance(o, TextOutput) and "No user message available" in o.content
        for o in result.outputs
    )
