# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the agent-location probe and its use in /activity."""

import asyncio

import pytest
from nemo_oo_agents_cli.tui.agent_location import (
    AgentLocation,
    locate_agent_on_loop,
)


@pytest.mark.asyncio
async def test_locate_walks_full_await_chain():
    """The probe follows cr_await down to the actual suspend point."""

    async def tool_layer():
        await asyncio.sleep(5)

    async def cell_layer():
        await tool_layer()

    # The probe matches tasks whose coroutine/name contains the turn method.
    async def handle():  # noqa: ANN202 - name is what the probe matches on
        await cell_layer()

    task = asyncio.ensure_future(handle())
    await asyncio.sleep(0.02)  # let it suspend inside asyncio.sleep
    try:
        loc = locate_agent_on_loop()
        assert isinstance(loc, AgentLocation)
        quals = [f.qualname for f in loc.stack]
        # outermost-first: handle -> cell_layer -> tool_layer -> sleep
        assert any("handle" in q for q in quals)
        assert any("cell_layer" in q for q in quals)
        assert any("tool_layer" in q for q in quals)
        # innermost is the suspend point
        assert loc.innermost is loc.stack[-1]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_locate_descends_into_awaited_task():
    """A directly-awaited child Task is followed, not stopped at."""

    async def child():
        await asyncio.sleep(5)

    async def handle():
        await asyncio.ensure_future(child())

    task = asyncio.ensure_future(handle())
    await asyncio.sleep(0.02)
    try:
        loc = locate_agent_on_loop()
        assert loc is not None
        quals = [f.qualname for f in loc.stack]
        assert any("handle" in q for q in quals)
        assert any("child" in q for q in quals)
    finally:
        task.cancel()
        # handle awaits a child task; cancelling parent cancels the await
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_locate_descends_through_gather():
    """asyncio.gather blocks on a _GatheringFuture; the walk still reaches a child.

    This is the Doer-subagent path (parent gathers concurrent sub-tasks). The
    probe follows the gathering future's child tasks rather than stopping at the
    gather frame.
    """

    async def child():
        await asyncio.sleep(5)

    async def handle():
        await asyncio.gather(child())

    task = asyncio.ensure_future(handle())
    await asyncio.sleep(0.02)
    try:
        loc = locate_agent_on_loop()
        assert loc is not None
        quals = [f.qualname for f in loc.stack]
        assert any("handle" in q for q in quals)
        assert any("child" in q for q in quals), "expected to descend into a gathered child"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_locate_returns_none_when_no_turn_running():
    """With no matching turn task, the probe returns None (idle)."""
    loc = locate_agent_on_loop(turn_method="__no_such_method__")
    assert loc is None


@pytest.mark.asyncio
async def test_activity_command_includes_suspend_location():
    """/activity surfaces the await stack when a turn is in flight."""
    from unittest.mock import AsyncMock, MagicMock

    from nemo_oo_agents_cli.tui.commands import ActivityCommand
    from nemo_oo_agents_cli.tui.output import TableOutput

    cmd = ActivityCommand(frontend=AsyncMock(), config=MagicMock(), agent=MagicMock())

    async def tool_layer():
        await asyncio.sleep(5)

    async def handle():
        await tool_layer()

    task = asyncio.ensure_future(handle())
    await asyncio.sleep(0.02)
    try:
        result = await cmd.execute([])
        assert result.success
        table = result.outputs[0]
        assert isinstance(table, TableOutput)
        flat = "\n".join(cell for row in table.rows for cell in row)
        assert "Suspended at" in flat
        assert "tool_layer" in flat
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_innermost_frame_has_source_context():
    """The suspend-point frame carries ±3 lines of source (this test file)."""

    async def handle():
        await asyncio.sleep(5)

    task = asyncio.ensure_future(handle())
    await asyncio.sleep(0.02)
    try:
        loc = locate_agent_on_loop()
        assert loc is not None and loc.innermost is not None
        ctx = loc.innermost.context
        # asyncio.sleep is C-adjacent but resolves to the sleep() coroutine in
        # the stdlib, which has real source — so context should be non-empty.
        assert ctx, "expected source context for the suspend frame"
        current = [s for s in ctx if s.is_current]
        assert len(current) == 1
        assert current[0].lineno == loc.innermost.lineno
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_activity_command_emits_highlighted_code(monkeypatch):
    """/activity emits the suspend-point source as a CodeExecution block.

    Simulates a CodeAct/REPL cell by registering fake source in linecache under
    the frame's co_filename and pointing the probe's innermost frame at it. The
    code block is a separate output so the frontend can syntax-highlight it.
    """
    import linecache

    from nemo_oo_agents_cli.tui import agent_location
    from nemo_oo_agents_cli.tui.commands import ActivityCommand
    from nemo_oo_agents_cli.tui.output import CodeExecution, TableOutput

    cell_name = "Cell In[42]"
    cell_src = "a = 1\nb = 2\nc = await tool()\nd = 4\ne = 5\n"
    linecache.cache[cell_name] = (
        len(cell_src),
        None,
        cell_src.splitlines(keepends=True),
        cell_name,
    )

    fake = agent_location.AgentLocation(
        task_name="TUIAgent.handle",
        stack=[agent_location.FrameInfo("handle", 3, cell_name)],
    )
    fake.stack[-1].context = agent_location._source_context(cell_name, 3)

    from unittest.mock import AsyncMock, MagicMock

    cmd = ActivityCommand(frontend=AsyncMock(), config=MagicMock(), agent=MagicMock())
    monkeypatch.setattr(cmd, "_locate_agent", lambda: fake)

    try:
        result = await cmd.execute([])
        # First output is the activity table; second is the code block.
        assert isinstance(result.outputs[0], TableOutput)
        code = next(o for o in result.outputs if isinstance(o, CodeExecution))
        # the snippet covers the suspend line plus context, in source order
        assert "c = await tool()" in code.code
        assert "a = 1" in code.code
        assert code.code.splitlines() == ["a = 1", "b = 2", "c = await tool()", "d = 4", "e = 5"]
        # numbered from the real file offset, with the suspend line highlighted
        assert code.start_line == 1  # context starts at file line 1 here
        assert code.highlight_line == 3  # "c = await tool()" is file line 3
    finally:
        linecache.cache.pop(cell_name, None)


@pytest.mark.asyncio
async def test_activity_idle_is_one_liner():
    """When idle with no turn running, /activity is a single status line."""
    from unittest.mock import AsyncMock, MagicMock

    from nemo_oo_agents_cli.tui.commands import ActivityCommand
    from nemo_oo_agents_cli.tui.output import TableOutput, TextOutput

    cmd = ActivityCommand(frontend=AsyncMock(), config=MagicMock(), agent=MagicMock())
    monkeypatch_loc = lambda: None  # noqa: E731 - probe returns None when idle
    cmd._locate_agent = monkeypatch_loc  # type: ignore[method-assign]

    result = await cmd.execute([])
    assert result.success
    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert isinstance(out, TextOutput)
    assert not isinstance(out, TableOutput)
    assert "idle" in out.content.lower()
