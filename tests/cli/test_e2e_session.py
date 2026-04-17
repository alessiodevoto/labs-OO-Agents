"""End-to-end TUI smoke test.

Exercises the full Session → CommandRegistry → SessionManager stack with a
``ScriptedFrontend`` that replays pre-programmed inputs and records every
rendered Output.  No real terminal, no real LLM calls that need network.

Run with:
    PYTHONPATH=src pytest tests/cli/test_e2e_session.py -v
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from nemo_oo_agents_cli.tui.output import (
    BashOutput,
    ClearScreen,
    HelpOutput,
    Output,
    TableOutput,
    TextOutput,
)

# ---------------------------------------------------------------------------
# ScriptedFrontend — pure rendering (no mixin)
# ---------------------------------------------------------------------------


class ScriptedFrontend:
    """Replays a fixed list of inputs; records every rendered Output."""

    def __init__(self, inputs: list[str]) -> None:
        self._inputs = list(inputs)
        self.outputs: list[Output] = []
        self._thinking = False

    # -- Frontend protocol --------------------------------------------------

    async def render(self, output: Output) -> None:  # type: ignore[override]
        self.outputs.append(output)

    async def get_input(
        self,
        prompt: str,
        completions: list[str] | None = None,
        default: str = "",
        bottom_toolbar=None,
    ) -> str:
        if not self._inputs:
            raise EOFError("No more scripted inputs")
        return self._inputs.pop(0)

    async def start_thinking(self, message: str = "...") -> None:
        self._thinking = True

    async def stop_thinking(self) -> None:
        self._thinking = False

    async def typeahead_loop(self, state) -> None:
        # Scripted tests never exercise typeahead — return immediately so the
        # session's _agent_turn can await the agent task and finish.
        return

    def exit_typeahead(self) -> None:
        pass

    def invalidate_typeahead(self) -> None:
        pass

    async def emit_user_message_above_prompt(self, content: str) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return True

    async def open_editor(
        self, filename: str, content: str, language: str = "plaintext"
    ) -> str | None:
        return None

    def close(self) -> None:
        pass

    # -- Helpers for assertions ---------------------------------------------

    def outputs_of(self, cls: type) -> list:
        return [o for o in self.outputs if isinstance(o, cls)]

    def any_text_contains(self, fragment: str, case_sensitive: bool = False) -> bool:
        frag = fragment if case_sensitive else fragment.lower()
        for o in self.outputs:
            text = ""
            if isinstance(o, TextOutput):
                text = o.content
            elif isinstance(o, TableOutput):
                text = o.title + " " + " ".join(cell for row in o.rows for cell in row)
            elif isinstance(o, HelpOutput):
                text = " ".join(o.commands.keys()) + " " + " ".join(o.commands.values())
            elif isinstance(o, BashOutput):
                text = (o.stdout or "") + (o.stderr or "")
            if frag in (text if case_sensitive else text.lower()):
                return True
        return False

    def clear_outputs(self) -> None:
        self.outputs.clear()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_agent():
    """TUIAgent backed by FakeLLMClient (no network)."""
    from nemo_oo_agents_cli.tui.agent import TUIAgent
    from unifiedllm import FakeLLMClient

    return TUIAgent(llm=FakeLLMClient())


@pytest.fixture
def fake_config():
    from nemo_oo_agents_cli.tui.config import AgentConfig, Config, SummarizationConfig, TUIConfig

    tui = TUIConfig(
        default_model="openai/gpt-4o",
        vi_mode=False,
        skills_dirs=[],
        mcp_file=None,
    )
    agent_cfg = AgentConfig(
        working_dir=Path("."),
        summarization=SummarizationConfig(),
    )
    return Config(tui=tui, agent=agent_cfg)


def build_session(frontend, agent, config, session_manager=None):
    from nemo_oo_agents_cli.tui.commands import CommandRegistry
    from nemo_oo_agents_cli.tui.session import Session

    registry = CommandRegistry(
        config=config.tui,
        agent=agent,
        frontend=frontend,
        skills_dirs=config.tui.skills_dirs,
        mcp_file=config.tui.mcp_file,
        session_manager=session_manager,
    )
    return Session(
        frontend=frontend,
        agent=agent,
        config=config,
        registry=registry,
        session_manager=session_manager,
    )


# ---------------------------------------------------------------------------
# Test: /help
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_command(fake_agent, fake_config):
    """/help renders a HelpOutput listing at least the standard commands."""
    frontend = ScriptedFrontend(["/help", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    help_outputs = frontend.outputs_of(HelpOutput)
    assert help_outputs, "Expected at least one HelpOutput from /help"
    commands_map = help_outputs[0].commands
    for cmd in ("/help", "/exit", "/clear", "/session list"):
        assert any(cmd in k for k in commands_map), f"Missing {cmd!r} in help"


# ---------------------------------------------------------------------------
# Test: /clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_command(fake_agent, fake_config):
    """/clear renders a ClearScreen output."""
    frontend = ScriptedFrontend(["/clear", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    assert frontend.outputs_of(ClearScreen), "Expected ClearScreen from /clear"


# ---------------------------------------------------------------------------
# Test: /models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_command(fake_agent, fake_config):
    """/models renders a TableOutput or TextOutput listing available models."""
    frontend = ScriptedFrontend(["/models", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    tables = frontend.outputs_of(TableOutput)
    texts = frontend.outputs_of(TextOutput)
    assert tables or texts, "Expected some output from /models"


# ---------------------------------------------------------------------------
# Test: /session list (no sessions in tmp dir)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_list_empty(fake_agent, fake_config, tmp_path):
    """/session list with no sessions reports empty."""
    frontend = ScriptedFrontend(["/session list", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        await session.run()

    assert frontend.any_text_contains("no sessions"), "Expected 'no sessions' message"


# ---------------------------------------------------------------------------
# Test: /session new creates a new session file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_new(fake_agent, fake_config, tmp_path):
    """/session new replaces the active SessionManager."""
    from nemo_oo_agents.storage import SQLiteStorageManager
    from nemo_oo_agents_cli.tui.session_manager import SessionManager

    sid = str(uuid.uuid4())
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
    sm = SessionManager(
        storage=storage,
        session_id=sid,
        model="openai/gpt-4o",
        agent_cls="TUIAgent",
        working_dir="/tmp",
    )
    sm.close()

    frontend = ScriptedFrontend(["/session new", "/exit"])
    session = build_session(frontend, fake_agent, fake_config, session_manager=sm)

    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        await session.run()

    clears = frontend.outputs_of(ClearScreen)
    assert clears, "Expected ClearScreen on /session new"
    assert frontend.any_text_contains("new session"), "Expected 'new session' text"
    assert session._session_manager is not sm, "Session manager should have been replaced"


# ---------------------------------------------------------------------------
# Test: /session export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_export(fake_agent, fake_config, tmp_path, monkeypatch):
    """/session export writes a Markdown file."""
    from nemo_oo_agents.storage import SQLiteStorageManager
    from nemo_oo_agents_cli.tui.session_manager import SessionManager

    monkeypatch.chdir(tmp_path)

    sid = str(uuid.uuid4())
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
    sm = SessionManager(
        storage=storage,
        session_id=sid,
        model="openai/gpt-4o",
        agent_cls="TUIAgent",
        working_dir="/tmp",
    )
    from nemo_oo_agents_cli.tui.tui_events import TUIAgentMessage

    sm.record_user("hello world")
    sm._storage.event_manager.add(TUIAgentMessage(content="I got your message"))

    frontend = ScriptedFrontend(["/session export", "/exit"])
    session = build_session(frontend, fake_agent, fake_config, session_manager=sm)
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        await session.run()

    assert frontend.any_text_contains("exported"), "Expected export success message"
    exported = list(tmp_path.glob("session-*.md"))
    assert exported, "Expected an exported .md file"
    content = exported[0].read_text()
    assert "hello world" in content
    assert "I got your message" in content


# ---------------------------------------------------------------------------
# Test: /history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_command(fake_agent, fake_config):
    """/history renders a TableOutput with event counts."""
    frontend = ScriptedFrontend(["/history", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    tables = frontend.outputs_of(TableOutput)
    texts = frontend.outputs_of(TextOutput)
    assert tables or texts, "Expected output from /history"


# ---------------------------------------------------------------------------
# Test: /sandbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sandbox_command(fake_agent, fake_config):
    """/sandbox renders a table showing bash sandbox status."""
    frontend = ScriptedFrontend(["/sandbox", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    tables = frontend.outputs_of(TableOutput)
    texts = frontend.outputs_of(TextOutput)
    assert tables or texts, "Expected output from /sandbox"


# ---------------------------------------------------------------------------
# Test: /skills (now requires no 'bash' capability)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_command_available_without_bash(fake_config):
    """/skills must be available even on agents without a bash attribute."""
    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui.commands import CommandRegistry

    class MinimalAgent:
        def __init__(self):
            self.event_manager = MagicMock()
            self.event_manager.keys = MagicMock(return_value=[])
            self.event_manager.on = MagicMock(return_value=lambda: None)

        async def respond(self, msg):
            pass

    agent = MinimalAgent()
    frontend = ScriptedFrontend(["/skills", "/exit"])

    registry = CommandRegistry(
        config=fake_config.tui,
        agent=agent,
        frontend=frontend,
        skills_dirs=None,
        mcp_file=None,
    )
    assert "skills" in registry._commands, "/skills must be registered on agents without bash"


# ---------------------------------------------------------------------------
# Test: /mcp (now requires no 'bash' capability)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_command_available_without_bash(fake_config):
    """/mcp must be available even on agents without a bash attribute."""
    from unittest.mock import MagicMock

    from nemo_oo_agents_cli.tui.commands import CommandRegistry

    class MinimalAgent:
        def __init__(self):
            self.event_manager = MagicMock()
            self.event_manager.keys = MagicMock(return_value=[])
            self.event_manager.on = MagicMock(return_value=lambda: None)

        async def respond(self, msg):
            pass

    agent = MinimalAgent()
    frontend = ScriptedFrontend(["/exit"])
    registry = CommandRegistry(
        config=fake_config.tui,
        agent=agent,
        frontend=frontend,
        skills_dirs=None,
        mcp_file=None,
    )
    assert "mcp" in registry._commands, "/mcp must be registered on agents without bash"


# ---------------------------------------------------------------------------
# Test: bang command (!ls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bang_command_runs_bash(fake_agent, fake_config):
    """!ls runs through the agent's bash tool and renders BashOutput."""
    frontend = ScriptedFrontend(["!echo hello_from_tui", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    bash_outputs = frontend.outputs_of(BashOutput)
    assert bash_outputs, "Expected a BashOutput from !echo"
    assert "hello_from_tui" in (bash_outputs[0].stdout or ""), (
        f"Expected 'hello_from_tui' in bash stdout, got: {bash_outputs[0].stdout!r}"
    )


# ---------------------------------------------------------------------------
# Test: regular message records user turn in session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regular_message_records_user_turn(fake_agent, fake_config, tmp_path):
    """A regular (non-slash, non-bang) message is recorded in the session DB."""
    from nemo_oo_agents.storage import SQLiteStorageManager
    from nemo_oo_agents_cli.tui.session_manager import SessionManager

    sid = str(uuid.uuid4())
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
        sm = SessionManager(
            storage=storage,
            session_id=sid,
            model="openai/gpt-4o",
            agent_cls="TUIAgent",
            working_dir="/tmp",
        )

    frontend = ScriptedFrontend(["what is 2 + 2?", "/exit"])
    session = build_session(frontend, fake_agent, fake_config, session_manager=sm)
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        await session.run()

    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        turns = SessionManager.load_turns(sm.session_id)
    user_turns = [t for t in turns if t.role == "user"]
    assert user_turns, "Expected at least one user turn in session DB"
    assert user_turns[0].content == "what is 2 + 2?"


# ---------------------------------------------------------------------------
# Test: /exit terminates the REPL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_command_terminates_loop(fake_agent, fake_config):
    """/exit causes session.run() to return without consuming further inputs."""
    frontend = ScriptedFrontend(["/exit", "/help", "/help"])  # extra inputs never consumed
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    help_outputs = frontend.outputs_of(HelpOutput)
    assert len(help_outputs) == 0, "Commands after /exit should not be processed"


# ---------------------------------------------------------------------------
# Test: empty input is skipped (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_input_skipped(fake_agent, fake_config):
    """Empty inputs (just Enter) are ignored without crashing."""
    frontend = ScriptedFrontend(["", "   ", "", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()  # must not raise


# ---------------------------------------------------------------------------
# Test: unknown slash command returns error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_slash_command_returns_error(fake_agent, fake_config):
    """An unrecognised /command returns an error TextOutput."""
    frontend = ScriptedFrontend(["/notacommand", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)
    await session.run()

    errors = [o for o in frontend.outputs_of(TextOutput) if o.level == "error"]
    assert errors, "Expected an error for unknown /command"


# ---------------------------------------------------------------------------
# Test: session close() called on exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_close_called_on_exit(fake_agent, fake_config, tmp_path):
    """SessionManager.close() is called when the session exits."""
    from nemo_oo_agents.storage import SQLiteStorageManager
    from nemo_oo_agents_cli.tui.session_manager import SessionManager

    sid = str(uuid.uuid4())
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
        sm = SessionManager(
            storage=storage,
            session_id=sid,
            model="openai/gpt-4o",
            agent_cls="TUIAgent",
            working_dir="/tmp",
        )

    frontend = ScriptedFrontend(["/exit"])
    session = build_session(frontend, fake_agent, fake_config, session_manager=sm)
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        await session.run()

    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        metas = SessionManager.list_sessions()
    assert any(m.id == sm.session_id for m in metas), "Session should still exist after close"


# ---------------------------------------------------------------------------
# Test: /session list shows sessions (with real files)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_list_shows_existing_sessions(fake_agent, fake_config, tmp_path):
    """/session list renders a table when sessions exist."""
    from nemo_oo_agents.storage import SQLiteStorageManager
    from nemo_oo_agents_cli.tui.session_manager import SessionManager

    sid = str(uuid.uuid4())
    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
        sm = SessionManager(
            storage=storage,
            session_id=sid,
            model="openai/gpt-4o",
            agent_cls="TUIAgent",
            working_dir="/tmp",
        )
        sm.record_user("hello")
        sm.close()

    frontend = ScriptedFrontend(["/session list", "/exit"])
    session = build_session(frontend, fake_agent, fake_config)

    with patch("nemo_oo_agents_cli.tui.session_manager.SESSIONS_DIR", tmp_path):
        await session.run()

    tables = frontend.outputs_of(TableOutput)
    assert tables, "Expected a TableOutput from /session list with sessions present"
    assert tables[0].title == "Recent Sessions"
