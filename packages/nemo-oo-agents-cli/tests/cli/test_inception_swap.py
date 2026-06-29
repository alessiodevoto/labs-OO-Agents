# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for TUIApplication.swap_agent — the slash-inception hot-swap seam."""

from __future__ import annotations

import asyncio

import pytest
from nemo_oo_agents_cli.tui.tui_application import TUIApplication

from .tui_app_harness import FakeAgent, ThreadGate


@pytest.mark.asyncio
async def test_swap_agent_redirects_dispatcher_to_new_agent():
    """After swap_agent, queued input is handled by the NEW agent, not the old.

    Models slash-inception: the new agent shares the old agent's live channels;
    swap_agent restarts the dispatcher bound to the new agent so a message put
    on the shared user_messages queue is delivered to ``new.handle``.
    """
    old = FakeAgent()
    new = FakeAgent()

    # slash-inception shares the OLD agent's live channels onto the NEW agent.
    new.queue_manager = old.queue_manager
    new._user_messages_in = old._user_messages_in
    new.user_messages = old.user_messages

    app = TUIApplication(agent=old)

    # Start the dispatcher on the old agent with one message.
    app.submit_message("for-old")
    # Let the dispatcher pick it up.
    for _ in range(50):
        await asyncio.sleep(0)
        if old.messages_received:
            break
    assert old.messages_received == ["for-old"]

    # Hot-swap onto the new agent and queue a seed prompt on the shared channel.
    new._user_messages_in.put("for-new")
    await app.swap_agent(new)

    # The fresh dispatcher (bound to new) drains the shared queue → new.handle.
    for _ in range(100):
        await asyncio.sleep(0)
        if new.messages_received:
            break
    assert new.messages_received == ["for-new"]
    # The old agent must NOT have seen the post-swap message.
    assert "for-new" not in old.messages_received
    assert app.agent is new

    # Cleanup: end the dispatcher.
    if app._agent_task is not None and not app._agent_task.done():
        app._agent_task.cancel()
        try:
            await app._agent_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_swap_agent_restarts_dispatcher_in_two_loop_tui() -> None:
    """swap_agent restarts the dispatcher when the TUI uses a separate agent loop."""
    old = FakeAgent()
    new = FakeAgent()
    new.queue_manager = old.queue_manager
    new._user_messages_in = old._user_messages_in
    new.user_messages = old.user_messages

    step_started = ThreadGate()

    async def step(_self: FakeAgent, _msg: str) -> None:
        step_started.set()
        await asyncio.Future()

    old.queue(step)
    app = TUIApplication(agent=old)
    agent_loop = app._ensure_agent_loop()
    try:
        app.submit_message("for-old")
        await asyncio.wait_for(step_started.wait(), timeout=1.0)
        new._user_messages_in.put("for-new")
        await app.agent_run_async(lambda: app.swap_agent(new))
        for _ in range(200):
            if new.messages_received:
                break
            await asyncio.sleep(0.01)
        assert new.messages_received == ["for-new"]
        assert app.agent is new
    finally:
        if app._agent_task is not None and not app._agent_task.done():
            app._agent_task.cancel()
            try:
                await app._agent_task
            except (asyncio.CancelledError, Exception):
                pass
        await app._stop_agent_loop()
        assert not agent_loop.is_running()
