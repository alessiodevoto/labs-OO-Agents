# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TuiSessionResumed is emitted after the agent is reconstituted (bootstrap + /session resume)."""

import pytest
from nemo_oo_agents_cli.tui.bootstrap import bootstrap
from nemo_oo_agents_cli.tui.config import Config

from nemo_oo_agents.events import TuiSessionResumed


def test_event_is_runtime_role_and_fields():
    e = TuiSessionResumed(session_id="abc", restored=True)
    assert e.session_id == "abc"
    assert e.restored is True
    # Runtime events never enter conversation/LLM context.
    from nemo_oo_agents.context_blocks.models import Role

    assert type(e)._role is Role.RUNTIME_EVENT


def test_event_registered_as_core_type():
    # The storage layer registers core event types for deserialization.
    from nemo_oo_agents.storage import sqlite as _sql

    assert "TuiSessionResumed" in _sql._CORE_TYPES


@pytest.mark.asyncio
async def test_bootstrap_emits_event_to_subscribers_on_fresh_session():
    """TuiSessionResumed is a runtime event (emit-only) — observe it via on().

    Runtime events are never recorded/queryable, so a skill must subscribe with
    event_manager.on("TuiSessionResumed", handler) — which is exactly how
    agent_mesh will auto-reconnect. We assert the handler fires once with the
    right payload.

    Because bootstrap() emits during construction (before we can subscribe), we
    re-emit through the same manager to exercise the subscriber path the skills
    use, and separately assert bootstrap reached the emit (no exception, agent
    built with a session_id).
    """
    from nemo_oo_agents.events import TuiSessionResumed

    result = await bootstrap(Config())
    agent = result.agent
    assert result.session_id is not None

    seen = []
    agent.event_manager.register_event_type(TuiSessionResumed)
    agent.event_manager.on("TuiSessionResumed", lambda e: seen.append(e))

    agent.event_manager.add(TuiSessionResumed(session_id=result.session_id, restored=False))

    assert len(seen) == 1
    assert seen[0].restored is False
    assert seen[0].session_id == result.session_id


@pytest.mark.asyncio
async def test_on_handler_receives_event():
    """A subscriber registered before emit receives the event (the skill path)."""
    from nemo_oo_agents import Agent
    from nemo_oo_agents.events import TuiSessionResumed
    from nemo_oo_agents.unifiedllm import FakeLLMClient

    class _A(Agent, llm=FakeLLMClient()):
        pass

    agent = _A()
    agent.event_manager.register_event_type(TuiSessionResumed)
    got = []
    agent.event_manager.on("TuiSessionResumed", lambda e: got.append((e.session_id, e.restored)))
    agent.event_manager.add(TuiSessionResumed(session_id="sess-1", restored=True))
    assert got == [("sess-1", True)]


@pytest.mark.asyncio
async def test_bootstrap_resume_without_snapshot_emits_restored_false(monkeypatch):
    """-c on a session with no snapshot must emit restored=False, not True.

    Regression: `restored` was set to `_resumed` ("the user passed -c"), which
    lied when no snapshot was actually applied. We capture the event bootstrap
    *itself* emits (by tee-ing EventManager.add) and assert that payload
    directly — not a re-emitted one.
    """
    from nemo_oo_agents.events import TuiSessionResumed
    from nemo_oo_agents.runtime.event_manager import EventManager

    captured: list = []
    real_add = EventManager.add

    def _tee_add(self, event, **kwargs):
        if isinstance(event, TuiSessionResumed):
            captured.append(event)
        return real_add(self, event, **kwargs)

    monkeypatch.setattr(EventManager, "add", _tee_add)

    # Fresh session first (resumable id, zero snapshots), then resume it:
    # bootstrap restores nothing -> must emit restored=False.
    first = await bootstrap(Config())
    sid = first.session_id
    captured.clear()  # drop the fresh-session emit; we want the resume one

    await bootstrap(Config(), resume_session_id=sid)

    # The resume bootstrap emits exactly one TuiSessionResumed; whatever its
    # session_id, it must report restored=False because no snapshot was applied
    # (the requested session had none). Previously this wrongly emitted True.
    assert len(captured) == 1, f"expected one resume emit, got {captured!r}"
    assert captured[0].restored is False
