# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for TUIApplication.swap_agent — the slash-inception hot-swap seam."""

from __future__ import annotations

import asyncio

import pytest
from nemo_oo_agents_cli.tui.tui_application import TUIApplication

from .tui_app_harness import FakeAgent


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
    app.swap_agent(new)

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
