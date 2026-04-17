# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for type-ahead message queue in _agent_turn.

These tests drive the Session at a behavioural level: they verify that input
entered while the agent is running gets queued and delivered as the next
``respond()`` call, that slash commands are queued separately, and that
interrupts do not drop queued text.

The new session flow uses ``frontend.typeahead_loop(state)`` as a stay-open
prompt during agent work. The mock below implements that contract.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from nemo_oo_agents.storage import SQLiteStorageManager
from nemo_oo_agents_cli.tui.agent import RespondResult
from nemo_oo_agents_cli.tui.commands import CommandRegistry
from nemo_oo_agents_cli.tui.output import TextOutput
from nemo_oo_agents_cli.tui.queue_state import QueueState
from nemo_oo_agents_cli.tui.session import Session
from nemo_oo_agents_cli.tui.session_manager import SessionManager

# ---------------------------------------------------------------------------
# Test frontend that simulates delayed typeahead input
# ---------------------------------------------------------------------------


class TypeAheadFrontend:
    """Frontend mock with two input channels:

    - ``initial_inputs`` feed ``get_input()`` (the between-turns REPL prompt).
    - ``typeahead_inputs`` are submitted to ``QueueState`` inside
      ``typeahead_loop()`` to simulate the user typing while the agent works.

    ``typeahead_loop()`` submits every typeahead input and then waits for
    ``exit_typeahead()``. Optionally a sentinel (``_INTERRUPT``) inside the
    typeahead list raises EOFError to simulate Ctrl+D.
    """

    __test__ = False

    _INTERRUPT = object()
    _CANCEL = object()

    def __init__(
        self,
        initial_inputs: list[str],
        typeahead_inputs: list | None = None,
    ):
        self._initial = list(initial_inputs)
        self._typeahead = list(typeahead_inputs or [])
        self._exit_event = asyncio.Event()
        self.outputs: list = []

    async def render(self, output) -> None:
        self.outputs.append(output)

    async def get_input(
        self,
        prompt: str,
        completions=None,
        default: str = "",
        bottom_toolbar=None,
    ) -> str:
        if self._initial:
            return self._initial.pop(0)
        raise EOFError

    async def start_thinking(self, message: str = "thinking...") -> None:
        pass

    async def stop_thinking(self) -> None:
        pass

    async def typeahead_loop(self, state: QueueState) -> None:
        # Give the agent a moment to start before submitting typeahead
        await asyncio.sleep(0)
        while self._typeahead:
            item = self._typeahead.pop(0)
            if item is self._INTERRUPT:
                raise EOFError
            if item is self._CANCEL:
                # Simulate the Esc keybinding: set the flag and return
                # (the real binding calls app.exit() which makes prompt_async
                # return).
                state.cancel_requested = True
                return
            state.submit(item)
        # Wait for exit_typeahead() (agent finished)
        await self._exit_event.wait()
        self._exit_event.clear()

    def exit_typeahead(self) -> None:
        self._exit_event.set()

    def invalidate_typeahead(self) -> None:
        pass

    async def emit_user_message_above_prompt(self, content: str) -> None:
        from nemo_oo_agents_cli.tui.output import UserMessage

        self.outputs.append(UserMessage(content=content))

    @property
    def is_connected(self) -> bool:
        return True

    async def open_editor(self, filename, content, language="plaintext"):
        return None

    def close(self) -> None:
        pass

    def text_contents(self) -> list[str]:
        return [o.content for o in self.outputs if isinstance(o, TextOutput)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sm(tmp_path):
    sid = str(uuid.uuid4())
    storage = SQLiteStorageManager(tmp_path / f"{sid}.db")
    return SessionManager(
        storage=storage, session_id=sid, model="test", agent_cls="A", working_dir=""
    )


def _make_slow_agent(*, delay: float = 0.1, responses=None):
    """Agent whose respond() takes ``delay`` seconds so typeahead has time to fire."""
    agent = MagicMock()
    agent._llm = MagicMock()
    agent.event_manager = MagicMock()
    agent.event_manager.on.return_value = lambda: None
    agent.event_manager.keys.return_value = []
    agent._summarizers = []
    agent.context_manager = {}

    if responses is None:
        responses = [RespondResult.WAIT_FOR_USER_INPUT]

    call_count = 0

    async def _slow_respond(msg):
        nonlocal call_count
        await asyncio.sleep(delay)
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    agent.respond = AsyncMock(side_effect=_slow_respond)
    return agent


def _make_session(tmp_path, frontend, agent=None):
    if agent is None:
        agent = _make_slow_agent()
    sm = _make_sm(tmp_path)
    config = MagicMock()
    config.tui.show_python = False
    config.tui.vi_mode = False
    config.default_model = "test-model"
    registry = CommandRegistry(
        frontend=frontend,
        config=config,
        agent=agent,
        session_manager=sm,
        skills_dirs=None,
        mcp_file=None,
    )
    return Session(
        frontend=frontend,
        agent=agent,
        config=config,
        registry=registry,
        session_manager=sm,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTypeAhead:
    @pytest.mark.asyncio
    async def test_queued_message_delivered_to_respond(self, tmp_path):
        """First respond() gets the initial message; /exit terminates."""
        frontend = TypeAheadFrontend(initial_inputs=["hello", "/exit"])
        agent = _make_slow_agent(delay=0.05)
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        calls = [c.args[0] for c in agent.respond.call_args_list]
        assert calls[0] == "hello"

    @pytest.mark.asyncio
    async def test_pending_commands_executed_after_turn(self, tmp_path):
        """Slash commands queued during agent work run after the turn."""
        frontend = TypeAheadFrontend(initial_inputs=["hello"])
        agent = _make_slow_agent(delay=0.05)
        session = _make_session(tmp_path, frontend, agent)
        session._pending_commands.append("/help")
        await session.run()

        from nemo_oo_agents_cli.tui.output import HelpOutput

        assert any(isinstance(o, HelpOutput) for o in frontend.outputs)

    @pytest.mark.asyncio
    async def test_multiple_inputs_combined_into_one_message(self, tmp_path):
        """Two typeahead lines become a single multi-line respond() call."""
        frontend = TypeAheadFrontend(
            initial_inputs=["start"],
            typeahead_inputs=["line one", "line two"],
        )
        agent = _make_slow_agent(
            delay=0.3,
            responses=[
                RespondResult.WAIT_FOR_USER_INPUT,
                RespondResult.WAIT_FOR_USER_INPUT,
            ],
        )
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        calls = [c.args[0] for c in agent.respond.call_args_list]
        assert len(calls) >= 2
        assert "line one" in calls[1]
        assert "line two" in calls[1]

    @pytest.mark.asyncio
    async def test_slash_command_queued_separately(self, tmp_path):
        """Slash commands typed during agent work are NOT sent to respond()."""
        frontend = TypeAheadFrontend(
            initial_inputs=["work on this", "/exit"],
            typeahead_inputs=["/help"],
        )
        agent = _make_slow_agent(delay=0.2)
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        calls = [c.args[0] for c in agent.respond.call_args_list]
        assert calls[0] == "work on this"
        assert not any("/help" in c for c in calls)

    @pytest.mark.asyncio
    async def test_no_typeahead_normal_flow(self, tmp_path):
        """Without typeahead input, agent runs once and returns normally."""
        frontend = TypeAheadFrontend(
            initial_inputs=["hello", "/exit"],
            typeahead_inputs=[],
        )
        agent = _make_slow_agent(delay=0.05)
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        calls = [c.args[0] for c in agent.respond.call_args_list]
        assert calls[0] == "hello"
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_context_block_cleaned_up_after_turn(self, tmp_path):
        """queued_messages context block is removed after the agent turn."""
        frontend = TypeAheadFrontend(
            initial_inputs=["start", "/exit"],
            typeahead_inputs=["queued msg"],
        )
        agent = _make_slow_agent(delay=0.2)
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        assert "queued_messages" not in agent.context_manager

    @pytest.mark.asyncio
    async def test_interrupt_during_agent_work(self, tmp_path):
        """EOFError from typeahead_loop interrupts the agent and surfaces a warning."""
        frontend = TypeAheadFrontend(
            initial_inputs=["do stuff"],
            typeahead_inputs=[TypeAheadFrontend._INTERRUPT],
        )
        agent = _make_slow_agent(delay=0.5)
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        texts = frontend.text_contents()
        assert any("interrupt" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_esc_cancels_and_ends_turn_when_queue_empty(self, tmp_path):
        """Esc with no queued messages cancels the agent and ends the turn."""
        frontend = TypeAheadFrontend(
            initial_inputs=["first", "/exit"],
            typeahead_inputs=[TypeAheadFrontend._CANCEL],
        )
        agent = _make_slow_agent(
            delay=1.0,
            responses=[RespondResult.CONTINUE_WORKING],  # would loop forever without cancel
        )
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        calls = [c.args[0] for c in agent.respond.call_args_list]
        # Only the initial respond() was called — Esc cancelled it, queue was
        # empty, turn ended, /exit stopped the loop.
        assert len(calls) == 1
        assert calls[0] == "first"

    @pytest.mark.asyncio
    async def test_esc_delivers_queued_messages_as_new_respond(self, tmp_path):
        """Esc with queued messages cancels and delivers them as next respond()."""
        frontend = TypeAheadFrontend(
            initial_inputs=["first", "/exit"],
            typeahead_inputs=[
                "queued before esc",
                TypeAheadFrontend._CANCEL,
            ],
        )
        agent = _make_slow_agent(
            delay=1.0,
            responses=[
                RespondResult.CONTINUE_WORKING,
                RespondResult.WAIT_FOR_USER_INPUT,
            ],
        )
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        calls = [c.args[0] for c in agent.respond.call_args_list]
        # First respond() with "first" got cancelled by Esc, second respond()
        # delivered the queued message.
        assert len(calls) >= 2
        assert calls[0] == "first"
        assert "queued before esc" in calls[1]

    @pytest.mark.asyncio
    async def test_queued_message_rendered_to_scrollback_before_delivery(self, tmp_path):
        """Queued text is committed as a UserMessage output before going to the agent."""
        from nemo_oo_agents_cli.tui.output import UserMessage

        frontend = TypeAheadFrontend(
            initial_inputs=["start"],
            typeahead_inputs=["queued text"],
        )
        agent = _make_slow_agent(
            delay=0.1,
            responses=[
                RespondResult.WAIT_FOR_USER_INPUT,
                RespondResult.WAIT_FOR_USER_INPUT,
            ],
        )
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        user_msgs = [o for o in frontend.outputs if isinstance(o, UserMessage)]
        assert any("queued text" in m.content for m in user_msgs), (
            "queued message should appear as a UserMessage output before being delivered"
        )

    @pytest.mark.asyncio
    async def test_typeahead_works_across_multiple_rounds(self, tmp_path):
        """User can keep queueing during round 2 (delivering queued) — no special-case skip."""

        # Custom frontend: different typeahead per call to typeahead_loop
        class MultiRoundFrontend(TypeAheadFrontend):
            def __init__(self):
                super().__init__(initial_inputs=["first"])
                self._per_round = [
                    ["round1-msg"],
                    ["round2-msg"],
                ]
                self._call = 0

            async def typeahead_loop(self, state):
                await asyncio.sleep(0)
                items = self._per_round[self._call] if self._call < len(self._per_round) else []
                self._call += 1
                for it in items:
                    state.submit(it)
                await self._exit_event.wait()
                self._exit_event.clear()

        frontend = MultiRoundFrontend()
        agent = _make_slow_agent(
            delay=0.2,
            responses=[
                RespondResult.WAIT_FOR_USER_INPUT,  # round 1: first → queue round1-msg
                RespondResult.WAIT_FOR_USER_INPUT,  # round 2: round1-msg → queue round2-msg
                RespondResult.WAIT_FOR_USER_INPUT,  # round 3: round2-msg → done
            ],
        )
        session = _make_session(tmp_path, frontend, agent)
        await session.run()

        calls = [c.args[0] for c in agent.respond.call_args_list]
        # Three respond calls: initial, round-1-queued, round-2-queued
        assert len(calls) >= 3
        assert calls[0] == "first"
        assert "round1-msg" in calls[1]
        assert "round2-msg" in calls[2]
        # typeahead_loop ran three times — once per agent call
        assert frontend._call == 3
