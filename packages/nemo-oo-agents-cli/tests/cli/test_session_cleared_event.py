# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TuiSessionCleared is emitted after /clear resets the agent's working state."""

import pytest

from nemo_oo_agents.events import TuiSessionCleared


def test_event_is_runtime_role_and_fields():
    from nemo_oo_agents.context_blocks.models import Role

    e = TuiSessionCleared(session_id="abc")
    assert e.session_id == "abc"
    assert type(e)._role is Role.RUNTIME_EVENT
    # session_id is optional (may be unknown at clear time).
    assert TuiSessionCleared().session_id is None


def test_event_registered_as_core_type():
    from nemo_oo_agents.storage import sqlite as _sql

    assert "TuiSessionCleared" in _sql._CORE_TYPES


@pytest.mark.asyncio
async def test_reset_emits_cleared_event_to_subscribers():
    """_reset_agent_working_state emits TuiSessionCleared; a subscriber receives it."""
    from nemo_oo_agents_cli.tui.commands import _reset_agent_working_state

    from nemo_oo_agents import Agent
    from nemo_oo_agents.unifiedllm import FakeLLMClient

    class _A(Agent, llm=FakeLLMClient()):
        pass

    agent = _A()
    agent.event_manager.register_event_type(TuiSessionCleared)
    seen = []
    agent.event_manager.on("TuiSessionCleared", lambda e: seen.append(e))

    await _reset_agent_working_state(agent)

    assert len(seen) == 1
    assert isinstance(seen[0], TuiSessionCleared)
