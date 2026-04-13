"""Comprehensive TUI integration tests — every slash command via Session.run().

Every test drives the full stack:  TestFrontend → Session.run() →
CommandHandler → Command.execute() → CommandResult → TestFrontend.outputs.

This exercises routing, output dispatch, and Session-level side-effects (swap,
exit, bang prefix) that command-unit tests in test_commands.py cannot reach.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nemo_oo_agents.storage import SQLiteStorageManager
from nemo_oo_agents_cli.tui.commands import CommandRegistry
from nemo_oo_agents_cli.tui.output import (
    ClearScreen,
    HelpOutput,
    HistoryReplay,
    TableOutput,
)
from nemo_oo_agents_cli.tui.session import Session
from nemo_oo_agents_cli.tui.session_manager import SessionManager
from nemo_oo_agents_cli.tui.testing import TestFrontend

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_sm(tmp_path, *, model="test-model", session_id=None):
    sid = session_id or str(uuid.uuid4())
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
    return SessionManager(
        storage=storage, session_id=sid, model=model, agent_cls="A", working_dir=""
    )


def _make_agent(*, with_bash=True, with_summarization=True):
    """Create a mock agent with the common capabilities wired up."""
    agent = MagicMock()
    agent._llm = MagicMock()

    # event_manager — needs on() to return a callable (unsubscribe fn)
    agent.event_manager = MagicMock()
    agent.event_manager.on.return_value = lambda: None
    agent.event_manager.keys.return_value = []
    agent._summarizers = []

    agent.respond = AsyncMock()

    if with_bash:
        agent.bash = MagicMock()
        agent.bash.use_sandbox = False
        agent.bash.sandbox_available = True
        agent.bash.run = AsyncMock(
            return_value=MagicMock(stdout="output", stderr="", return_code=0)
        )
    elif hasattr(agent, "bash"):
        del agent.bash

    if with_summarization:
        agent.get_summarization_status = MagicMock(
            return_value={
                "active_events": 5,
                "policy": "auto",
                "has_summarizer": False,
                "max_tokens": 100_000,
                "current_tokens": 20_000,
                "preserve_recent": 5,
                "summary_count": 0,
                "summary_tags": [],
            }
        )
    return agent


def _make_session(
    tmp_path,
    inputs: list[str],
    *,
    agent=None,
    session_manager=None,
):
    """Create a Session driven by TestFrontend with *inputs*."""
    if agent is None:
        agent = _make_agent()
    if session_manager is None:
        session_manager = _make_sm(tmp_path)

    frontend = TestFrontend(inputs=inputs)

    config = MagicMock()
    config.tui.show_python = False
    config.tui.vi_mode = False
    config.default_model = "test-model"
    config.show_python = False  # PythonCommand reads self.config.show_python

    registry = CommandRegistry(
        frontend=frontend,
        config=config,
        agent=agent,
        session_manager=session_manager,
        skills_dirs=None,
        mcp_file=None,
    )
    session = Session(
        frontend=frontend,
        agent=agent,
        config=config,
        registry=registry,
        session_manager=session_manager,
    )
    return session, frontend, agent, config


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_help_renders_help_output(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/help", "/exit"])
        await session.run()
        assert frontend.outputs_of(HelpOutput), "Expected HelpOutput from /help"

    @pytest.mark.asyncio
    async def test_help_includes_known_commands(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/help", "/exit"])
        await session.run()
        helps = frontend.outputs_of(HelpOutput)
        cmds = helps[0].commands
        assert "/exit" in cmds or "/help" in cmds


# ---------------------------------------------------------------------------
# /exit and /quit
# ---------------------------------------------------------------------------


class TestExitCommand:
    @pytest.mark.asyncio
    async def test_exit_terminates_loop(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/exit"])
        await session.run()
        texts = frontend.text_contents()
        assert any("Goodbye" in t for t in texts)

    @pytest.mark.asyncio
    async def test_quit_terminates_loop(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/quit"])
        await session.run()
        texts = frontend.text_contents()
        assert any("Goodbye" in t for t in texts)

    @pytest.mark.asyncio
    async def test_eof_terminates_loop(self, tmp_path):
        """Empty input queue → EOFError → clean exit."""
        session, frontend, _, _ = _make_session(tmp_path, [])  # no inputs
        await session.run()
        # The loop should exit cleanly (no exception propagation)


# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------


class TestClearCommand:
    @pytest.mark.asyncio
    async def test_clear_renders_clear_screen(self, tmp_path):
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            session, frontend, _, _ = _make_session(tmp_path, ["/clear", "/exit"])
            await session.run()
        assert frontend.outputs_of(ClearScreen), "Expected ClearScreen from /clear"

    @pytest.mark.asyncio
    async def test_clear_success_message(self, tmp_path):
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            session, frontend, _, _ = _make_session(tmp_path, ["/clear", "/exit"])
            await session.run()
        assert any("session" in t.lower() for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /model
# ---------------------------------------------------------------------------


class TestModelCommand:
    @pytest.mark.asyncio
    async def test_model_shows_current_model(self, tmp_path):
        session, frontend, _, config = _make_session(tmp_path, ["/model", "/exit"])
        config.default_model = "openai/gpt-4o"
        await session.run()
        assert any("openai/gpt-4o" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_model_with_args_returns_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/model extra-arg", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /models
# ---------------------------------------------------------------------------


class TestModelsCommand:
    @pytest.mark.asyncio
    async def test_models_renders_table(self, tmp_path):
        with patch("unifiedllm.MODELS", {"provider/model-a": None, "provider/model-b": None}):
            session, frontend, _, _ = _make_session(tmp_path, ["/models", "/exit"])
            await session.run()
        tables = frontend.outputs_of(TableOutput)
        assert tables, "Expected TableOutput from /models"
        assert tables[0].title == "Available Models"

    @pytest.mark.asyncio
    async def test_models_marks_current(self, tmp_path):
        with patch("unifiedllm.MODELS", {"current-model": None, "other-model": None}):
            session, frontend, _, config = _make_session(tmp_path, ["/models", "/exit"])
            config.default_model = "current-model"
            await session.run()
        tables = frontend.outputs_of(TableOutput)
        rows_flat = [cell for row in tables[0].rows for cell in row]
        assert "→" in rows_flat


# ---------------------------------------------------------------------------
# /switch
# ---------------------------------------------------------------------------


class TestSwitchCommand:
    @pytest.mark.asyncio
    async def test_switch_valid_model(self, tmp_path):
        """SwitchCommand now requires model as argument: /switch <model>."""
        with patch("unifiedllm.MODELS", {"new-model": None}):
            with patch("unifiedllm.get_llm_client", return_value=MagicMock()):
                session, frontend, _, _ = _make_session(tmp_path, ["/switch new-model", "/exit"])
                await session.run()
        assert any("new-model" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_switch_invalid_model(self, tmp_path):
        with patch("unifiedllm.MODELS", {"valid-model": None}):
            session, frontend, _, _ = _make_session(
                tmp_path, ["/switch nonexistent-model", "/exit"]
            )
            await session.run()
        assert any("not found" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_switch_no_args_error(self, tmp_path):
        with patch("unifiedllm.MODELS", {"a-model": None}):
            session, frontend, _, _ = _make_session(tmp_path, ["/switch", "/exit"])
            await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /theme
# ---------------------------------------------------------------------------


class TestThemeCommand:
    @pytest.mark.asyncio
    async def test_theme_mocha(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/theme mocha", "/exit"])
        await session.run()
        assert any("mocha" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_theme_latte(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/theme latte", "/exit"])
        await session.run()
        assert any("latte" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_theme_invalid(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/theme badtheme", "/exit"])
        await session.run()
        assert any("badtheme" in t or "Theme must" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_theme_no_args(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/theme", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------


class TestHistoryCommand:
    @pytest.mark.asyncio
    async def test_history_status_renders_table(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/history status", "/exit"])
        await session.run()
        tables = frontend.outputs_of(TableOutput)
        assert tables, "Expected TableOutput from /history status"
        assert tables[0].title == "History Status"

    @pytest.mark.asyncio
    async def test_history_tags_renders_table(self, tmp_path):
        agent = _make_agent()
        agent.event_manager.keys.return_value = ["tag1", "tag2"]
        event_mock = MagicMock()
        event_mock.event_type = "user_event"
        agent.event_manager.__getitem__ = MagicMock(return_value=event_mock)

        session, frontend, _, _ = _make_session(tmp_path, ["/history tags", "/exit"], agent=agent)
        await session.run()
        tables = frontend.outputs_of(TableOutput)
        assert tables, "Expected TableOutput from /history tags"
        assert "tag1" in tables[0].rows[0][0]

    @pytest.mark.asyncio
    async def test_history_no_args_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/history", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_history_invalid_subcommand(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/history bogus", "/exit"])
        await session.run()
        assert any("bogus" in t or "Unknown" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /sandbox
# ---------------------------------------------------------------------------


class TestSandboxCommand:
    @pytest.mark.asyncio
    async def test_sandbox_status_renders_table(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/sandbox status", "/exit"])
        await session.run()
        assert frontend.outputs_of(TableOutput)

    @pytest.mark.asyncio
    async def test_sandbox_enable(self, tmp_path):
        agent = _make_agent()
        agent.bash.sandbox_available = True
        agent.bash.use_sandbox = False
        session, frontend, _, _ = _make_session(tmp_path, ["/sandbox enable", "/exit"], agent=agent)
        await session.run()
        assert agent.bash.use_sandbox is True
        assert any("enabled" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_sandbox_enable_already_enabled(self, tmp_path):
        agent = _make_agent()
        agent.bash.sandbox_available = True
        agent.bash.use_sandbox = True
        session, frontend, _, _ = _make_session(tmp_path, ["/sandbox enable", "/exit"], agent=agent)
        await session.run()
        assert any("already" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_sandbox_enable_srt_unavailable(self, tmp_path):
        agent = _make_agent()
        agent.bash.sandbox_available = False
        session, frontend, _, _ = _make_session(tmp_path, ["/sandbox enable", "/exit"], agent=agent)
        await session.run()
        assert any("not available" in t.lower() or "SRT" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_sandbox_disable(self, tmp_path):
        agent = _make_agent()
        agent.bash.use_sandbox = True
        session, frontend, _, _ = _make_session(
            tmp_path, ["/sandbox disable", "/exit"], agent=agent
        )
        await session.run()
        assert agent.bash.use_sandbox is False
        assert any("disabled" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_sandbox_no_args_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/sandbox", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /python
# ---------------------------------------------------------------------------


class TestPythonCommand:
    @pytest.mark.asyncio
    async def test_python_status(self, tmp_path):
        session, frontend, _, config = _make_session(tmp_path, ["/python status", "/exit"])
        config.show_python = False
        await session.run()
        assert any("Python execution display" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_python_on(self, tmp_path):
        session, frontend, _, config = _make_session(tmp_path, ["/python on", "/exit"])
        config.show_python = False
        await session.run()
        assert any("enabled" in t.lower() for t in frontend.text_contents())
        assert config.show_python is True

    @pytest.mark.asyncio
    async def test_python_off(self, tmp_path):
        session, frontend, _, config = _make_session(tmp_path, ["/python off", "/exit"])
        config.show_python = True
        await session.run()
        assert any("suppressed" in t.lower() for t in frontend.text_contents())
        assert config.show_python is False

    @pytest.mark.asyncio
    async def test_python_no_args_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/python", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /compact
# ---------------------------------------------------------------------------


class TestCompactCommand:
    @pytest.mark.asyncio
    async def test_compact_empty_history(self, tmp_path):
        agent = _make_agent()
        agent.event_manager.keys.return_value = []
        session, frontend, _, _ = _make_session(tmp_path, ["/compact", "/exit"], agent=agent)
        await session.run()
        assert any("empty" in t.lower() or "Nothing" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_compact_with_events_no_summarizer(self, tmp_path):
        agent = _make_agent()
        agent.event_manager.keys.return_value = ["t1", "t2", "t3"]
        agent._summarizers = []
        session, frontend, _, _ = _make_session(tmp_path, ["/compact", "/exit"], agent=agent)
        await session.run()
        agent.event_manager.clear.assert_called()
        assert any("Cleared" in t or "history" in t.lower() for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /edit
# ---------------------------------------------------------------------------


class TestEditCommand:
    @pytest.mark.asyncio
    async def test_edit_cancelled(self, tmp_path):
        """open_editor() returns None → 'Edit cancelled.'"""
        session, frontend, _, _ = _make_session(tmp_path, ["/edit some_file.py", "/exit"])
        # TestFrontend.open_editor returns None by default
        await session.run()
        assert any("cancelled" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_edit_no_changes(self, tmp_path):
        """open_editor() returns same content → 'No changes.'"""
        target = tmp_path / "existing.py"
        target.write_text("original content")

        from nemo_oo_agents_cli.tui.testing import TestFrontend as _TF

        class SameContentFrontend(_TF):
            __test__ = False

            async def open_editor(self, filename, content, language="plaintext"):
                return content  # unchanged

        frontend = SameContentFrontend(inputs=[f"/edit {target}", "/exit"])
        agent = _make_agent()
        config = MagicMock()
        config.tui.show_python = False
        config.tui.vi_mode = False
        config.default_model = "test-model"
        config.show_python = False
        sm = _make_sm(tmp_path)
        registry = CommandRegistry(
            frontend=frontend,
            config=config,
            agent=agent,
            session_manager=sm,
            skills_dirs=None,
            mcp_file=None,
        )
        session = Session(
            frontend=frontend, agent=agent, config=config, registry=registry, session_manager=sm
        )
        await session.run()
        assert any("No changes" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_edit_with_changes_saves_file(self, tmp_path):
        """open_editor() returns new content → file written + success message."""
        target = tmp_path / "edited.py"
        target.write_text("original")

        from nemo_oo_agents_cli.tui.testing import TestFrontend as _TF

        class EditingFrontend(_TF):
            __test__ = False

            async def open_editor(self, filename, content, language="plaintext"):
                return "new content"

        frontend = EditingFrontend(inputs=[f"/edit {target}", "/exit"])
        agent = _make_agent()
        config = MagicMock()
        config.tui.show_python = False
        config.tui.vi_mode = False
        config.default_model = "test-model"
        config.show_python = False
        sm = _make_sm(tmp_path)
        registry = CommandRegistry(
            frontend=frontend,
            config=config,
            agent=agent,
            session_manager=sm,
            skills_dirs=None,
            mcp_file=None,
        )
        session = Session(
            frontend=frontend, agent=agent, config=config, registry=registry, session_manager=sm
        )
        await session.run()
        assert target.read_text() == "new content"
        assert any("Saved" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_edit_no_args_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/edit", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /mcp
# ---------------------------------------------------------------------------


class TestMCPCommand:
    @pytest.mark.asyncio
    async def test_mcp_list(self, tmp_path):
        mock_mcp = MagicMock()
        mock_mcp.MCPManager.list_servers.return_value = ["srv1", "srv2"]
        with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp}):
            session, frontend, _, _ = _make_session(tmp_path, ["/mcp list", "/exit"])
            await session.run()
        assert frontend.outputs_of(TableOutput)

    @pytest.mark.asyncio
    async def test_mcp_no_args_error(self, tmp_path):
        mock_mcp = MagicMock()
        with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp}):
            session, frontend, _, _ = _make_session(tmp_path, ["/mcp", "/exit"])
            await session.run()
        assert any("Usage" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_mcp_not_installed(self, tmp_path):
        """No mcp_nemo_oo_agents module → error message."""
        with patch.dict("sys.modules", {"mcp_nemo_oo_agents": None}):
            session, frontend, _, _ = _make_session(tmp_path, ["/mcp list", "/exit"])
            await session.run()
        assert any("MCP not enabled" in t or "not enabled" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_mcp_connect_server_not_found(self, tmp_path):
        mock_mcp = MagicMock()
        mock_mcp.MCPManager.list_servers.return_value = ["srv1"]
        with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp}):
            session, frontend, _, _ = _make_session(tmp_path, ["/mcp connect nope", "/exit"])
            await session.run()
        assert any("not found" in t.lower() or "nope" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_mcp_connect_success(self, tmp_path):
        mock_mcp = MagicMock()
        mock_mcp.MCPManager.list_servers.return_value = ["myserver"]
        mock_mcp.MCPManager.create_from_server.return_value = MagicMock()
        with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp}):
            session, frontend, agent, _ = _make_session(
                tmp_path, ["/mcp connect myserver", "/exit"]
            )
            await session.run()
        assert any("connected" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_mcp_disconnect_not_connected(self, tmp_path):
        mock_mcp = MagicMock()
        mock_mcp.MCPManager.list_servers.return_value = ["myserver"]
        with patch.dict("sys.modules", {"mcp_nemo_oo_agents": mock_mcp}):
            session, frontend, _, _ = _make_session(tmp_path, ["/mcp disconnect myserver", "/exit"])
            await session.run()
        assert any("not connected" in t.lower() for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /skills
# ---------------------------------------------------------------------------


class TestSkillsCommand:
    @pytest.mark.asyncio
    async def test_skills_list_no_dirs(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/skills list", "/exit"])
        await session.run()
        assert any("No skills" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_skills_list_with_skills(self, tmp_path):
        skill = MagicMock()
        skill.id = "my-skill"
        skill.description = "does stuff"
        with patch("nemo_oo_agents.SkillManager") as mock_sm:
            mock_sm.discover.return_value = {"my-skill": skill}

            agent = _make_agent()
            sm = _make_sm(tmp_path)
            frontend = TestFrontend(inputs=["/skills list", "/exit"])
            config = MagicMock()
            config.tui.show_python = False
            config.tui.vi_mode = False
            config.default_model = "test-model"
            config.show_python = False
            registry = CommandRegistry(
                frontend=frontend,
                config=config,
                agent=agent,
                session_manager=sm,
                skills_dirs=[Path("/fake/skills")],
                mcp_file=None,
            )
            session = Session(
                frontend=frontend, agent=agent, config=config, registry=registry, session_manager=sm
            )
            await session.run()

        assert frontend.outputs_of(TableOutput)

    @pytest.mark.asyncio
    async def test_skills_activate_not_found(self, tmp_path):
        with patch("nemo_oo_agents.SkillManager") as mock_sm:
            mock_sm.discover.return_value = {}

            agent = _make_agent()
            sm = _make_sm(tmp_path)
            frontend = TestFrontend(inputs=["/skills activate my-skill", "/exit"])
            config = MagicMock()
            config.tui.show_python = False
            config.tui.vi_mode = False
            config.default_model = "test-model"
            config.show_python = False
            registry = CommandRegistry(
                frontend=frontend,
                config=config,
                agent=agent,
                session_manager=sm,
                skills_dirs=[Path("/fake/skills")],
                mcp_file=None,
            )
            session = Session(
                frontend=frontend, agent=agent, config=config, registry=registry, session_manager=sm
            )
            await session.run()

        assert any("not found" in t.lower() or "my-skill" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_skills_no_args_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/skills", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# /session
# ---------------------------------------------------------------------------


class TestSessionCommand:
    @pytest.mark.asyncio
    async def test_session_list_empty(self, tmp_path):
        # SESSIONS_DIR must be distinct from the DB storage dir so list_sessions
        # finds no *.db files (the current session's DB lives in tmp_path, not
        # in the sessions subdirectory).
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", sessions_dir):
            sm = _make_sm(tmp_path)  # DB stored in tmp_path, not sessions_dir
            session, frontend, _, _ = _make_session(
                tmp_path, ["/session list", "/exit"], session_manager=sm
            )
            await session.run()
        assert any("No sessions" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_session_list_with_sessions(self, tmp_path):
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm = _make_sm(tmp_path)
            sm.record_user("hello")

            session, frontend, _, _ = _make_session(
                tmp_path, ["/session list", "/exit"], session_manager=sm
            )
            await session.run()
        tables = frontend.outputs_of(TableOutput)
        assert tables, "Expected TableOutput from /session list"

    @pytest.mark.asyncio
    async def test_session_rename(self, tmp_path):
        sm = _make_sm(tmp_path)
        session, frontend, _, _ = _make_session(
            tmp_path, ["/session rename My Session", "/exit"], session_manager=sm
        )
        await session.run()
        assert any("My Session" in t for t in frontend.text_contents())
        assert sm.name == "My Session"

    @pytest.mark.asyncio
    async def test_session_new_creates_fresh_session(self, tmp_path):
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm = _make_sm(tmp_path)
            old_sid = sm.session_id
            session, frontend, _, _ = _make_session(
                tmp_path, ["/session new", "/exit"], session_manager=sm
            )
            await session.run()

        assert any(
            "Started new session" in t or "History cleared" in t for t in frontend.text_contents()
        )
        # Old session data must survive
        turns = SessionManager.load_turns(old_sid)
        assert isinstance(turns, list)  # may be empty but must not raise

    @pytest.mark.asyncio
    async def test_session_delete_not_found(self, tmp_path):
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            session, frontend, _, _ = _make_session(tmp_path, ["/session delete aaaabbbb", "/exit"])
            await session.run()
        assert any("not found" in t.lower() or "aaaabbbb" in t for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_session_delete_existing(self, tmp_path):
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            old_sm = _make_sm(tmp_path)
            old_sm.record_user("content to delete")
            old_sid = old_sm.session_id
            old_sm.close()

            session, frontend, _, _ = _make_session(
                tmp_path, [f"/session delete {old_sid[:8]}", "/exit"]
            )
            await session.run()

        assert any("deleted" in t.lower() for t in frontend.text_contents())
        turns = SessionManager.load_turns(old_sid)
        assert turns == []

    @pytest.mark.asyncio
    async def test_session_resume(self, tmp_path):
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            # Create a past session with a turn
            past_sm = _make_sm(tmp_path)
            past_sm.record_user("from the past")
            past_sid = past_sm.session_id
            past_sm.close()

            session, frontend, _, _ = _make_session(
                tmp_path, [f"/session resume {past_sid[:8]}", "/exit"]
            )
            await session.run()

        assert frontend.outputs_of(HistoryReplay), "Expected HistoryReplay from /session resume"

    @pytest.mark.asyncio
    async def test_session_export(self, tmp_path):
        sm = _make_sm(tmp_path)
        sm.record_user("exportable")
        session, frontend, _, _ = _make_session(
            tmp_path, ["/session export", "/exit"], session_manager=sm
        )
        await session.run()
        assert any(
            "exported" in t.lower() or "session" in t.lower() for t in frontend.text_contents()
        )

    @pytest.mark.asyncio
    async def test_session_no_args_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/session", "/exit"])
        await session.run()
        assert any("Usage" in t for t in frontend.text_contents())


# ---------------------------------------------------------------------------
# Session-level routing edge cases
# ---------------------------------------------------------------------------


class TestSessionRouting:
    @pytest.mark.asyncio
    async def test_regular_message_calls_agent_respond(self, tmp_path):
        """Non-command input routes to _agent_turn → agent.respond()."""
        agent = _make_agent()
        session, frontend, _, _ = _make_session(tmp_path, ["hello world", "/exit"], agent=agent)
        await session.run()
        agent.respond.assert_called_once_with("hello world")

    @pytest.mark.asyncio
    async def test_multiple_messages_in_sequence(self, tmp_path):
        agent = _make_agent()
        session, frontend, _, _ = _make_session(
            tmp_path, ["msg one", "msg two", "/exit"], agent=agent
        )
        await session.run()
        assert agent.respond.call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_command_returns_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/notacommand", "/exit"])
        await session.run()
        assert any(
            "unknown" in t.lower() or "notacommand" in t.lower() for t in frontend.text_contents()
        )

    @pytest.mark.asyncio
    async def test_empty_slash_returns_error(self, tmp_path):
        session, frontend, _, _ = _make_session(tmp_path, ["/", "/exit"])
        await session.run()
        assert any("Empty command" in t or "error" in t.lower() for t in frontend.text_contents())

    @pytest.mark.asyncio
    async def test_bang_prefix_runs_bash(self, tmp_path):
        """!cmd routes to _handle_bang which calls agent.bash.run()."""
        agent = _make_agent()
        session, frontend, _, _ = _make_session(tmp_path, ["!echo hi", "/exit"], agent=agent)
        await session.run()
        agent.bash.run.assert_called_once_with("echo hi")

    @pytest.mark.asyncio
    async def test_empty_input_skipped(self, tmp_path):
        """Empty string is silently skipped — agent.respond() not called."""
        agent = _make_agent()
        session, frontend, _, _ = _make_session(tmp_path, ["", "/exit"], agent=agent)
        await session.run()
        agent.respond.assert_not_called()
