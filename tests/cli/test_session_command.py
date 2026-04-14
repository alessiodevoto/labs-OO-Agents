"""Tests for SessionCommand (/session list|resume|delete|export)
and Session._agent_turn session-recording behaviour."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nemo_oo_agents.storage import SQLiteStorageManager
from nemo_oo_agents_cli.tui.commands import CommandHandler, CommandRegistry
from nemo_oo_agents_cli.tui.output import TableOutput, TextOutput
from nemo_oo_agents_cli.tui.session_manager import SessionManager

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_frontend():
    frontend = AsyncMock()
    frontend.render = AsyncMock()
    frontend.get_input = AsyncMock(return_value="")
    frontend.start_thinking = AsyncMock()
    frontend.stop_thinking = AsyncMock()
    frontend.show_python = True
    frontend.clear_streaming_state = MagicMock()
    return frontend


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.default_model = "test-model"
    return config


@pytest.fixture
def mock_agent(mock_config):
    agent = MagicMock()
    agent._llm = MagicMock()
    agent.event_manager = MagicMock()
    agent.event_manager.clear = MagicMock()
    agent.event_manager.keys = MagicMock(return_value=[])
    agent.bash = MagicMock()
    agent.bash.use_sandbox = False
    agent.bash.sandbox_available = True
    return agent


@pytest.fixture
def mock_session_manager():
    sm = MagicMock()
    sm.session_id = "abcdef12-0000-0000-0000-000000000000"
    sm.as_markdown = MagicMock(return_value="# Session content\n\nstuff")
    return sm


def make_registry(mock_frontend, mock_config, mock_agent, session_manager=None):
    return CommandRegistry(
        frontend=mock_frontend,
        config=mock_config,
        agent=mock_agent,
        skills_dirs=None,
        mcp_file=None,
        session_manager=session_manager,
    )


def make_handler(registry, mock_frontend):
    return CommandHandler(registry=registry, frontend=mock_frontend)


def _make_sm(tmp_path, *, model="m", agent_cls="A", working_dir="", session_id=None):
    """Create a SessionManager backed by a SQLite DB in tmp_path."""
    sid = session_id or str(uuid.uuid4())
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
    return SessionManager(
        storage=storage, session_id=sid, model=model, agent_cls=agent_cls, working_dir=working_dir
    )


# ---------------------------------------------------------------------------
# /session list
# ---------------------------------------------------------------------------


class TestSessionList:
    @pytest.mark.asyncio
    async def test_list_empty(self, mock_frontend, mock_config, mock_agent):
        """/session list with no sessions returns an info message."""
        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.commands.SessionManager.list_sessions", return_value=[]):
            result = await handler.handle("/session list")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("No sessions found" in o.content for o in text_outputs)

    @pytest.mark.asyncio
    async def test_list_non_empty(self, mock_frontend, mock_config, mock_agent):
        """/session list with sessions returns a TableOutput."""
        from nemo_oo_agents_cli.tui.session_manager import SessionMeta

        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        meta = SessionMeta(
            id="abcdef12-1111-1111-1111-000000000001",
            model="openai/gpt-4o",
            agent="TUIAgent",
            started_at=1_700_000_000.0,
            last_active=1_700_000_100.0,
            turn_count=3,
            working_dir="/home/user",
        )

        with patch(
            "nemo_oo_agents_cli.tui.commands.SessionManager.list_sessions", return_value=[meta]
        ):
            result = await handler.handle("/session list")

        assert result.success is True
        table_outputs = [o for o in result.outputs if isinstance(o, TableOutput)]
        assert len(table_outputs) == 1
        rows_flat = [cell for row in table_outputs[0].rows for cell in row]
        assert any("abcdef12" in cell for cell in rows_flat)
        assert any("3" in cell for cell in rows_flat)  # turn_count


# ---------------------------------------------------------------------------
# /session resume
# ---------------------------------------------------------------------------


class TestSessionResume:
    @pytest.mark.asyncio
    async def test_resume_not_found(self, mock_frontend, mock_config, mock_agent, tmp_path):
        """/session resume <id> with no matching session returns an error."""
        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            result = await handler.handle("/session resume deadbeef")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("deadbeef" in o.content and "not found" in o.content for o in text_outputs)

    @pytest.mark.asyncio
    async def test_resume_ambiguous_prefix(self, mock_frontend, mock_config, mock_agent, tmp_path):
        """/session resume <prefix> that matches multiple sessions returns an error."""
        sid1 = "abcdef00-aaaa-0000-0000-000000000001"
        sid2 = "abcdef00-bbbb-0000-0000-000000000002"
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm1 = _make_sm(tmp_path, session_id=sid1)
            sm1.close()
            sm2 = _make_sm(tmp_path, session_id=sid2)
            sm2.close()

        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            result = await handler.handle("/session resume abcdef00")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("Ambiguous" in o.content or "ambiguous" in o.content for o in text_outputs)

    @pytest.mark.asyncio
    async def test_resume_success_swaps_session_manager(
        self, mock_frontend, mock_config, mock_agent, tmp_path
    ):
        """/session resume restores snapshot and returns new_session_manager."""
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm = _make_sm(tmp_path)
            sm.record_user("hello")
            sid = sm.session_id
            sm.close()

        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            result = await handler.handle(f"/session resume {sid[:8]}")

        assert result.success is True
        assert result.new_session_manager is not None
        assert result.new_session_manager.session_id == sid


# ---------------------------------------------------------------------------
# /session delete
# ---------------------------------------------------------------------------


class TestSessionDelete:
    @pytest.mark.asyncio
    async def test_delete_not_found(self, mock_frontend, mock_config, mock_agent, tmp_path):
        """/session delete <id> with no matching session returns error."""
        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            result = await handler.handle("/session delete nosuchid")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("not found" in o.content.lower() for o in text_outputs)

    @pytest.mark.asyncio
    async def test_delete_ambiguous_prefix(self, mock_frontend, mock_config, mock_agent, tmp_path):
        """/session delete <prefix> that matches multiple sessions returns error."""
        sid1 = "xyzabc00-aaaa-0000-0000-000000000001"
        sid2 = "xyzabc00-bbbb-0000-0000-000000000002"
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm1 = _make_sm(tmp_path, session_id=sid1)
            sm1.close()
            sm2 = _make_sm(tmp_path, session_id=sid2)
            sm2.close()

        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            result = await handler.handle("/session delete xyzabc00")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("Ambiguous" in o.content or "ambiguous" in o.content for o in text_outputs)

    @pytest.mark.asyncio
    async def test_delete_success_exact_id(self, mock_frontend, mock_config, mock_agent, tmp_path):
        """/session delete with a full session ID succeeds and reports deletion."""
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm = _make_sm(tmp_path)
            sid = sm.session_id
            sm.close()

        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            result = await handler.handle(f"/session delete {sid}")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("deleted" in o.content.lower() for o in text_outputs)

    @pytest.mark.asyncio
    async def test_delete_success_prefix_match(
        self, mock_frontend, mock_config, mock_agent, tmp_path
    ):
        """/session delete <prefix> that matches exactly one session deletes it."""
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm = _make_sm(tmp_path)
            sid = sm.session_id
            sm.close()

        registry = make_registry(mock_frontend, mock_config, mock_agent)
        handler = make_handler(registry, mock_frontend)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            result = await handler.handle(f"/session delete {sid[:8]}")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("deleted" in o.content.lower() for o in text_outputs)

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            remaining = SessionManager.list_sessions()
        assert not any(m.id == sid for m in remaining)


# ---------------------------------------------------------------------------
# /session export
# ---------------------------------------------------------------------------


class TestSessionExport:
    @pytest.mark.asyncio
    async def test_export_no_session_manager(self, mock_frontend, mock_config, mock_agent):
        """/session export with no session_manager returns an error."""
        registry = make_registry(mock_frontend, mock_config, mock_agent, session_manager=None)
        handler = make_handler(registry, mock_frontend)

        result = await handler.handle("/session export")

        assert result.success is False
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("No active session" in o.content for o in text_outputs)

    @pytest.mark.asyncio
    async def test_export_writes_file(
        self, mock_frontend, mock_config, mock_agent, mock_session_manager, tmp_path, monkeypatch
    ):
        """/session export writes a Markdown file and reports its name."""
        monkeypatch.chdir(tmp_path)

        registry = make_registry(
            mock_frontend, mock_config, mock_agent, session_manager=mock_session_manager
        )
        handler = make_handler(registry, mock_frontend)

        result = await handler.handle("/session export")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any("exported" in o.content.lower() for o in text_outputs)
        exported_files = list(tmp_path.glob("session-*.md"))
        assert len(exported_files) == 1
        assert "# Session content" in exported_files[0].read_text()


# ---------------------------------------------------------------------------
# /session new — storage swap
# ---------------------------------------------------------------------------


class TestSessionNew:
    @pytest.mark.asyncio
    async def test_new_reports_success(self, mock_frontend, mock_config, mock_agent, tmp_path):
        """/session new returns a success message."""
        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            sm = _make_sm(tmp_path)
            registry = make_registry(mock_frontend, mock_config, mock_agent, session_manager=sm)
            handler = make_handler(registry, mock_frontend)
            result = await handler.handle("/session new")

        assert result.success is True
        text_outputs = [o for o in result.outputs if isinstance(o, TextOutput)]
        assert any(
            "new session" in o.content.lower() or "started" in o.content.lower()
            for o in text_outputs
        )

    def test_swap_session_manager_updates_agent_storage(self, tmp_path):
        """_swap_session_manager updates agent._storage to the new session's DB.

        Regression test: before the fix, _swap_session_manager closed the old
        SessionManager (and its SQLiteStorageManager) but left agent._storage
        pointing at that closed connection.  Any subsequent agent event write
        would raise "Cannot operate on a closed database."
        """
        from nemo_oo_agents_cli.tui.commands import CommandRegistry
        from nemo_oo_agents_cli.tui.session import Session
        from nemo_oo_agents_cli.tui.tui_events import TUIUserInput

        # Real storage shared by agent and first session manager (mirrors main.py)
        original_storage = SQLiteStorageManager(tmp_path / "orig.db")
        new_storage = SQLiteStorageManager(tmp_path / "new.db")

        agent = MagicMock()
        agent._storage = original_storage

        frontend = AsyncMock()
        config = MagicMock()
        config.tui = MagicMock()

        with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
            old_sm = SessionManager(
                storage=original_storage,
                session_id=str(uuid.uuid4()),
                model="m",
                agent_cls="A",
            )
            new_sm = SessionManager(
                storage=new_storage,
                session_id=str(uuid.uuid4()),
                model="m",
                agent_cls="A",
            )

        registry = CommandRegistry(
            config=config.tui,
            agent=agent,
            frontend=frontend,
            skills_dirs=[],
            mcp_file=None,
            session_manager=old_sm,
        )
        session = Session(
            frontend=frontend,
            agent=agent,
            config=config,
            registry=registry,
            session_manager=old_sm,
        )

        # Directly call the swap — this is what session.run() does after /session new
        session._swap_session_manager(new_sm)

        # Agent's storage must point to the new (open) DB, not the old (closed) one.
        assert agent._storage is new_storage, (
            "agent._storage was not updated to new_storage after _swap_session_manager"
        )
        # Writing an event to the agent's storage must not raise.
        agent._storage.event_manager.add(TUIUserInput(text="test"))


# ---------------------------------------------------------------------------
# Session._agent_turn — session recording
# ---------------------------------------------------------------------------


class TestAgentTurnSessionRecording:
    """Verify _agent_turn completes without errors with/without a session_manager.

    Message events are now stored automatically by the framework (Message derives
    from Metadata with record=True by default).
    """

    @pytest.mark.asyncio
    async def test_agent_turn_completes_with_session_manager(self, mock_frontend):
        """_agent_turn completes without error when a session_manager is present."""
        from nemo_oo_agents_cli.tui.agent import RespondResult
        from nemo_oo_agents_cli.tui.session import Session

        mock_agent = MagicMock()
        mock_agent.respond = AsyncMock(return_value=RespondResult.WAIT_FOR_USER_INPUT)
        mock_agent.event_manager = MagicMock()

        mock_config = MagicMock()
        mock_config.tui = MagicMock()
        mock_config.tui.default_model = "openai/gpt-4o"

        session = Session(
            frontend=mock_frontend,
            agent=mock_agent,
            config=mock_config,
            registry=MagicMock(),
            session_manager=MagicMock(),
        )

        await session._agent_turn("hello")  # must not raise

    @pytest.mark.asyncio
    async def test_agent_turn_completes_without_session_manager(self, mock_frontend):
        """_agent_turn completes without error when session_manager is None."""
        from nemo_oo_agents_cli.tui.agent import RespondResult
        from nemo_oo_agents_cli.tui.session import Session

        mock_agent = MagicMock()
        mock_agent.respond = AsyncMock(return_value=RespondResult.WAIT_FOR_USER_INPUT)
        mock_agent.event_manager = MagicMock()

        mock_config = MagicMock()
        mock_config.tui = MagicMock()
        mock_config.tui.default_model = "openai/gpt-4o"

        session = Session(
            frontend=mock_frontend,
            agent=mock_agent,
            config=mock_config,
            registry=MagicMock(),
            session_manager=None,
        )

        await session._agent_turn("hello")  # must not raise
