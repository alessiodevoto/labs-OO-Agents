# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression: /activity must not freeze the UI loop while the agent is busy.

Root cause of the reported flicker: ``ActivityCommand`` probed the agent
location via the *blocking* ``agent_run`` (``future.result(timeout=30)``),
which froze the prompt_toolkit UI loop while the agent loop was busy —
starving the block-queue consumer (``self.message()`` output stopped
appearing) and wedging the prompt redraw (status-bar / input flicker).

The fix routes the probe through the awaitable ``agent_run_async`` so the UI
loop keeps spinning. These tests pin that behaviour at the unit level.
"""

import asyncio
import threading

import pytest


def _make_busy_agent_loop():
    """Start an event loop on its own thread with a long-running task hogging it.

    Returns (loop, stop_event, thread). The loop is "busy" in the sense that a
    blocking ``future.result`` against it would have to wait for the hog task
    to yield — modelling the agent mid-LLM-call / mid-cell.
    """
    loop = asyncio.new_event_loop()
    started = threading.Event()
    stop = asyncio.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.call_soon(started.set)
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    started.wait(2.0)
    return loop, stop, t


@pytest.mark.asyncio
async def test_agent_run_async_does_not_block_calling_loop():
    """``agent_run_async`` yields to the calling loop while the agent loop works.

    We schedule a probe that only completes after a delay, and concurrently run
    a "UI heartbeat" coroutine on the calling loop. If ``agent_run_async``
    blocked the loop (the old ``future.result`` behaviour), the heartbeat would
    not tick until the probe returned. We assert it ticks meanwhile.
    """
    from nemo_oo_agents_cli.tui.tui_application import TUIApplication

    app = TUIApplication.__new__(TUIApplication)  # avoid full prompt_toolkit init
    loop, _stop, _t = _make_busy_agent_loop()
    app._agent_loop = loop

    probe_done = asyncio.Event()

    def slow_probe():
        # Runs on the agent loop; sleeps to model an in-flight turn.
        async def _inner():
            await asyncio.sleep(0.3)
            return "located"

        return _inner()

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while not probe_done.is_set():
            ticks += 1
            await asyncio.sleep(0.02)

    hb = asyncio.ensure_future(heartbeat())

    result = await app.agent_run_async(slow_probe)
    probe_done.set()
    await hb

    loop.call_soon_threadsafe(loop.stop)

    assert result == "located"
    # The UI loop kept ticking during the 0.3s probe — proof it was not blocked.
    assert ticks >= 5


@pytest.mark.asyncio
async def test_agent_run_async_inline_without_loop():
    """With no agent loop (tests / pre-startup) it runs inline, no await needed."""
    from nemo_oo_agents_cli.tui.tui_application import TUIApplication

    app = TUIApplication.__new__(TUIApplication)
    app._agent_loop = None

    assert await app.agent_run_async(lambda: 42) == 42


@pytest.mark.asyncio
async def test_activity_probe_uses_async_dispatch():
    """ActivityCommand routes the location probe through agent_run_async.

    Guards against a regression to the blocking ``agent_run`` call, which is
    what froze the UI loop. We record which dispatcher the command used.
    """
    from unittest.mock import AsyncMock, MagicMock

    from nemo_oo_agents_cli.tui.commands import ActivityCommand

    cmd = ActivityCommand(frontend=AsyncMock(), config=MagicMock(), agent=MagicMock())

    used = {"sync": False, "async": False}

    def sync_run(fn):
        used["sync"] = True
        return None

    async def async_run(fn):
        used["async"] = True
        return None

    cmd._agent_run = sync_run
    cmd._agent_run_async = async_run

    await cmd.execute([])

    assert used["async"] is True
    assert used["sync"] is False
