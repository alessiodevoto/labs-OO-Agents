# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for /goal-mode slash command and dispatcher goal-mode injection."""

import asyncio
from unittest.mock import MagicMock

import pytest
from nemo_oo_agents_cli.tui.commands import GoalModeCommand, KeepGoingCommand
from nemo_oo_agents_cli.tui.output import TextOutput

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# /goal-mode command tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_frontend():
    from unittest.mock import AsyncMock

    frontend = AsyncMock()
    frontend.render = AsyncMock()
    return frontend


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.goal_mode = False
    config.keep_going = False
    config.keep_going_model = None
    config.default_model = "test-model"
    return config


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent._llm = MagicMock()
    agent.event_manager = MagicMock()
    agent.event_manager.keys = MagicMock(return_value=[])
    return agent


@pytest.fixture
def goal_cmd(mock_frontend, mock_config, mock_agent):
    return GoalModeCommand(mock_frontend, mock_config, mock_agent)


class TestGoalModeCommand:
    """Test /goal-mode on|off|status."""

    async def test_status_off(self, goal_cmd, mock_config):
        mock_config.goal_mode = False
        result = await goal_cmd.execute([])
        assert result.success
        assert any("off" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_status_on(self, goal_cmd, mock_config):
        mock_config.goal_mode = True
        result = await goal_cmd.execute([])
        assert result.success
        assert any("on" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_turn_on(self, goal_cmd, mock_config):
        mock_config.goal_mode = False
        result = await goal_cmd.execute(["on"])
        assert result.success
        assert mock_config.goal_mode is True

    async def test_turn_off(self, goal_cmd, mock_config):
        mock_config.goal_mode = True
        result = await goal_cmd.execute(["off"])
        assert result.success
        assert mock_config.goal_mode is False

    def test_validate_no_args(self, goal_cmd):
        ok, msg = goal_cmd.validate_args([])
        assert ok

    def test_validate_bad_subcmd(self, goal_cmd):
        ok, msg = goal_cmd.validate_args(["maybe"])
        assert not ok
        assert "Unknown" in msg

    def test_validate_good(self, goal_cmd):
        for sub in ("on", "off"):
            ok, msg = goal_cmd.validate_args([sub])
            assert ok
            assert msg is None


@pytest.fixture
def keep_going_cmd(mock_frontend, mock_config, mock_agent):
    return KeepGoingCommand(mock_frontend, mock_config, mock_agent)


class TestKeepGoingCommand:
    """Test /keep-going on|off|status."""

    async def test_status_off(self, keep_going_cmd, mock_config):
        mock_config.keep_going = False
        result = await keep_going_cmd.execute([])
        assert result.success
        assert any("off" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_status_prefers_agent_sticky_var(self, keep_going_cmd, mock_config, mock_agent):
        mock_config.keep_going = False
        mock_agent.vars = {"tui_keep_going": True}
        result = await keep_going_cmd.execute([])
        assert result.success
        assert any("on" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_status_on(self, keep_going_cmd, mock_config):
        mock_config.keep_going = True
        result = await keep_going_cmd.execute([])
        assert result.success
        assert any("on" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_turn_on_fails_without_model(self, keep_going_cmd, mock_config):
        mock_config.keep_going = False
        mock_config.keep_going_model = None
        result = await keep_going_cmd.execute(["on"])
        assert not result.success
        assert any(
            "model is not configured" in o.content
            for o in result.outputs
            if isinstance(o, TextOutput)
        )
        assert mock_config.keep_going is False

    async def test_turn_on(self, keep_going_cmd, mock_config, monkeypatch, tmp_path):
        monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path))
        mock_config.keep_going = False
        mock_config.keep_going_model = "audit-model"
        result = await keep_going_cmd.execute(["on"])
        assert result.success
        assert mock_config.keep_going is True

    async def test_turn_on_sets_agent_sticky_var(
        self, keep_going_cmd, mock_config, mock_agent, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path))
        mock_config.keep_going_model = "audit-model"
        mock_agent.vars = {}
        result = await keep_going_cmd.execute(["on"])
        assert result.success
        assert mock_agent.vars["tui_keep_going"] is True

    async def test_turn_off(self, keep_going_cmd, mock_config, monkeypatch, tmp_path):
        monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path))
        mock_config.keep_going = True
        result = await keep_going_cmd.execute(["off"])
        assert result.success
        assert mock_config.keep_going is False

    def test_validate_no_args(self, keep_going_cmd):
        ok, msg = keep_going_cmd.validate_args([])
        assert ok

    async def test_set_model(self, keep_going_cmd, mock_config, mock_agent, monkeypatch, tmp_path):
        monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path))
        mock_agent.vars = {}
        result = await keep_going_cmd.execute(["model", "audit-model"])
        assert result.success
        assert mock_config.keep_going_model == "audit-model"
        assert mock_agent.vars["tui_keep_going_model"] == "audit-model"

    async def test_settings_file_persists_model_and_enabled(
        self, keep_going_cmd, mock_config, monkeypatch, tmp_path
    ):
        import yaml

        monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path))
        await keep_going_cmd.execute(["model", "audit-model"])
        await keep_going_cmd.execute(["on"])
        settings = yaml.safe_load((tmp_path / "settings.yaml").read_text())
        assert settings["tui"]["keep_going_model"] == "audit-model"
        assert settings["tui"]["keep_going"] is True

    async def test_settings_persistence_repairs_non_mapping_tui_section(
        self, keep_going_cmd, mock_config, monkeypatch, tmp_path
    ):
        import yaml

        monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path))
        (tmp_path / "settings.yaml").write_text("tui: broken\n")
        result = await keep_going_cmd.execute(["model", "audit-model"])
        assert result.success
        settings = yaml.safe_load((tmp_path / "settings.yaml").read_text())
        assert settings["tui"] == {"keep_going_model": "audit-model"}

    async def test_status_prefers_agent_sticky_model(self, keep_going_cmd, mock_config, mock_agent):
        mock_config.keep_going_model = "config-model"
        mock_agent.vars = {"tui_keep_going_model": "sticky-model"}
        result = await keep_going_cmd.execute([])
        assert result.success
        assert any("sticky-model" in o.content for o in result.outputs if isinstance(o, TextOutput))

    def test_validate_bad_subcmd(self, keep_going_cmd):
        ok, msg = keep_going_cmd.validate_args(["maybe"])
        assert not ok
        assert "Usage" in msg

    def test_validate_good(self, keep_going_cmd):
        for args in (["on"], ["off"], ["model", "audit-model"]):
            ok, msg = keep_going_cmd.validate_args(args)
            assert ok
            assert msg is None


# ---------------------------------------------------------------------------
# Dispatcher goal-mode injection tests
# ---------------------------------------------------------------------------


class TestGoalModeDispatcher:
    """Test that the dispatcher injects todo events when goal_mode is on."""

    async def test_goal_mode_injects_open_todo(self):
        """When goal_mode=on and there are open todos, dispatcher re-enters handle()."""
        from .tui_app_harness import FakeAgent, TUIHarness

        agent = FakeAgent()
        config = MagicMock()
        config.tui = MagicMock()
        config.tui.goal_mode = True

        # Give the agent a todo manager with an open todo
        from nemo_oo_agents.tools.todo import TodoManager

        agent.todo = TodoManager()
        agent.todo.add("Fix the bug")

        turns_seen: list[str] = []

        def record_and_stop(ag, msg):
            turns_seen.append(msg)
            if "You are in goal mode" in msg:
                # Turn off goal mode after injection to stop the loop
                config.tui.goal_mode = False

        agent.script = [record_and_stop, record_and_stop]

        async with TUIHarness(agent=agent, config=config) as h:
            await h.submit_async("start")
            # Wait until the agent processes both the initial message and the injected one
            await h.wait_for(lambda: len(turns_seen) >= 2, timeout=3.0)

        assert turns_seen[0] == "start"
        assert "You are in goal mode" in turns_seen[1]
        assert "Fix the bug" in turns_seen[1]

    async def test_goal_mode_prioritizes_queued_user_message_before_todo_injection(self):
        """If the user speaks while a turn is running, deliver that message before Goal-mode TODO."""
        from .tui_app_harness import FakeAgent, TUIHarness

        agent = FakeAgent()
        config = MagicMock()
        config.tui = MagicMock()
        config.tui.goal_mode = True

        from nemo_oo_agents.tools.todo import TodoManager

        agent.todo = TodoManager()
        agent.todo.add("Fix the bug")

        turns_seen: list[str] = []

        def queue_user_message(ag, msg):
            turns_seen.append(msg)
            ag._user_messages_in.put("user interruption")

        def record_and_stop_after_goal(ag, msg):
            turns_seen.append(msg)
            if "You are in goal mode" in msg:
                config.tui.goal_mode = False

        agent.script = [queue_user_message, record_and_stop_after_goal, record_and_stop_after_goal]

        async with TUIHarness(agent=agent, config=config) as h:
            await h.submit_async("start")
            await h.wait_for(lambda: len(turns_seen) >= 3, timeout=3.0)

        assert turns_seen[0] == "start"
        assert turns_seen[1] == "user interruption"
        assert "You are in goal mode" in turns_seen[2]
        assert "Fix the bug" in turns_seen[2]

    async def test_goal_mode_off_does_not_inject(self):
        """When goal_mode=off, no injection even with open todos."""
        from .tui_app_harness import FakeAgent, TUIHarness

        agent = FakeAgent()
        config = MagicMock()
        config.tui = MagicMock()
        config.tui.goal_mode = False

        from nemo_oo_agents.tools.todo import TodoManager

        agent.todo = TodoManager()
        agent.todo.add("Fix the bug")

        async with TUIHarness(agent=agent, config=config) as h:
            await h.submit_async("hello")
            await h.wait_for(lambda: agent.messages_received == ["hello"])
            # Give time for any injection to happen
            await asyncio.sleep(0.1)

        # Only the original message was received - no injection
        assert agent.messages_received == ["hello"]

    async def test_goal_mode_no_todos_does_not_inject(self):
        """When goal_mode=on but no open todos, no injection."""
        from .tui_app_harness import FakeAgent, TUIHarness

        agent = FakeAgent()
        config = MagicMock()
        config.tui = MagicMock()
        config.tui.goal_mode = True

        # No todo manager or empty todos
        from nemo_oo_agents.tools.todo import TodoManager

        agent.todo = TodoManager()

        async with TUIHarness(agent=agent, config=config) as h:
            await h.submit_async("hello")
            await h.wait_for(lambda: agent.messages_received == ["hello"])
            await asyncio.sleep(0.1)

        assert agent.messages_received == ["hello"]

    async def test_goal_mode_skips_blocked_todos(self):
        """Goal mode only injects non-blocked todos."""
        from .tui_app_harness import FakeAgent, TUIHarness

        agent = FakeAgent()
        config = MagicMock()
        config.tui = MagicMock()
        config.tui.goal_mode = True

        from nemo_oo_agents.tools.todo import TodoManager

        agent.todo = TodoManager()
        t1 = agent.todo.add("Prerequisite")
        agent.todo.add("Blocked task", deps=[t1.id])

        turns_seen: list[str] = []

        def record(ag, msg):
            turns_seen.append(msg)
            if "You are in goal mode" in msg:
                config.tui.goal_mode = False

        agent.script = [record, record]

        async with TUIHarness(agent=agent, config=config) as h:
            await h.submit_async("go")
            await h.wait_for(lambda: len(turns_seen) >= 2, timeout=3.0)

        # Should inject the non-blocked "Prerequisite" todo, not the blocked one
        assert "You are in goal mode" in turns_seen[1]
        assert "Prerequisite" in turns_seen[1]
        assert "Blocked task" not in turns_seen[1]
