"""Tests for WebFrontend correctness.

Covers:
- WebSocketDisconnect → EOFError conversion in get_input()
- _disconnected flag set on disconnect
- _serialise() warning for unknown Output types
- Concurrent _send() calls serialised by lock (no interleaving)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nemo_oo_agents_cli.tui.output import (
    AgentMessage,
    BashOutput,
    ClearScreen,
    CodeExecution,
    HelpOutput,
    RichOutput,
    StartupInfo,
    TableOutput,
    TextOutput,
    Thinking,
)

WebFrontend = pytest.importorskip(
    "nemo_oo_agents_cli.web.frontend",
    reason="WebFrontend not yet implemented",
).WebFrontend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frontend(recv_side_effect=None, send_ok=True):
    """Build a WebFrontend with a mock WebSocket."""
    ws = MagicMock()
    ws.send_text = AsyncMock()
    if recv_side_effect is not None:
        ws.receive_text = AsyncMock(side_effect=recv_side_effect)
    else:
        ws.receive_text = AsyncMock(
            return_value=json.dumps({"type": "user_input", "text": "hello"})
        )
    if not send_ok:
        ws.send_text = AsyncMock(side_effect=Exception("connection closed"))
    return WebFrontend(ws), ws


# ---------------------------------------------------------------------------
# get_input(): WebSocketDisconnect → EOFError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_input_disconnect_raises_eoferror():
    """WebSocketDisconnect during receive_text() must raise EOFError, not propagate raw."""
    from starlette.websockets import WebSocketDisconnect

    frontend, ws = _make_frontend(recv_side_effect=WebSocketDisconnect())
    # send_text (for prompt_request) must succeed
    ws.send_text = AsyncMock()

    with pytest.raises(EOFError, match="disconnected"):
        await frontend.get_input(">>> ")


@pytest.mark.asyncio
async def test_get_input_disconnect_sets_disconnected_flag():
    """_disconnected must be True after a WebSocketDisconnect in get_input()."""
    from starlette.websockets import WebSocketDisconnect

    frontend, ws = _make_frontend(recv_side_effect=WebSocketDisconnect())
    ws.send_text = AsyncMock()

    with pytest.raises(EOFError):
        await frontend.get_input(">>> ")

    assert frontend._disconnected is True


@pytest.mark.asyncio
async def test_get_input_disconnect_on_second_iteration():
    """WebSocketDisconnect on a non-first loop iteration is also caught."""
    from starlette.websockets import WebSocketDisconnect

    # First receive returns a ping (ignored), second raises disconnect
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(
        side_effect=[
            json.dumps({"type": "ping"}),  # first: ignored frame
            WebSocketDisconnect(),  # second: disconnect
        ]
    )

    frontend = WebFrontend(ws)
    with pytest.raises(EOFError):
        await frontend.get_input(">>> ")

    assert frontend._disconnected is True


@pytest.mark.asyncio
async def test_get_input_happy_path():
    """A normal user_input frame returns its text."""
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(return_value=json.dumps({"type": "user_input", "text": "  hi  "}))

    frontend = WebFrontend(ws)
    result = await frontend.get_input(">>> ")

    assert result == "hi"


@pytest.mark.asyncio
async def test_get_input_skips_non_user_input_frames():
    """Frames that aren't user_input are skipped until one arrives."""
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(
        side_effect=[
            json.dumps({"type": "ping"}),
            json.dumps({"type": "thinking_ack"}),
            json.dumps({"type": "user_input", "text": "the answer"}),
        ]
    )

    frontend = WebFrontend(ws)
    result = await frontend.get_input(">>> ")
    assert result == "the answer"


# ---------------------------------------------------------------------------
# _serialise(): warning for unknown Output type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serialise_unknown_output_type_raises():
    """An object without to_json() raises AttributeError — no silent drops."""

    class WeirdOutput:
        """Looks nothing like the known Output types."""

    frontend, _ = _make_frontend()
    with pytest.raises(AttributeError):
        frontend._serialise(WeirdOutput())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _serialise(): all built-in Output types produce non-None payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output, expected_type",
    [
        (TextOutput("hello", "info"), "text"),
        (TableOutput(columns=["A"], rows=[["v"]], title="T"), "table"),
        (HelpOutput(commands={"/help": "show help"}), "help"),
        (AgentMessage("agent says hi"), "agent_message"),
        (ClearScreen(), "clear"),
        (Thinking(active=True, message="thinking..."), "thinking_start"),
        (BashOutput(stdout="out", stderr="", return_code=0), "bash_output"),
        (RichOutput(kind="chart", data={}, title="C", fallback_text=""), "rich"),
    ],
)
async def test_serialise_builtin_output_types(output, expected_type):
    """All built-in Output types must serialise to a dict with the correct 'type' field."""
    frontend, _ = _make_frontend()
    result = frontend._serialise(output)

    assert result is not None, f"{type(output).__name__} must not serialise to None"
    assert result.get("type") == expected_type, (
        f"Expected type={expected_type!r}, got {result.get('type')!r}"
    )


@pytest.mark.asyncio
async def test_serialise_code_execution():
    """CodeExecution serialises to a 'code_execution' typed dict."""
    frontend, _ = _make_frontend()
    output = CodeExecution(
        tool_call_id="t1",
        code="print(1)",
        stdout="1\n",
        stderr=None,
        error=None,
        value=None,
    )
    result = frontend._serialise(output)
    assert result is not None
    assert result.get("type") == "code_execution"


@pytest.mark.asyncio
async def test_serialise_startup_info():
    """StartupInfo serialises to a 'startup' typed dict."""
    frontend, _ = _make_frontend()
    output = StartupInfo(
        model="openai/gpt-4o",
        short_model="gpt-4o",
        working_dir="/tmp",
        vi_mode=False,
    )
    result = frontend._serialise(output)
    assert result is not None
    assert result.get("type") == "startup"


# ---------------------------------------------------------------------------
# _send() lock: concurrent calls are serialised, not interleaved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_lock_serialises_concurrent_calls():
    """Multiple concurrent _send() calls must not overlap (lock is held per call)."""
    call_log: list[str] = []

    async def fake_send_text(text: str) -> None:
        call_log.append(f"start:{text}")
        await asyncio.sleep(0)  # yield to allow other coroutines to run
        call_log.append(f"end:{text}")

    ws = MagicMock()
    ws.send_text = fake_send_text
    frontend = WebFrontend(ws)

    await asyncio.gather(
        frontend._send({"type": "a"}),
        frontend._send({"type": "b"}),
        frontend._send({"type": "c"}),
    )

    # With locking, each start must be followed by its own end before the next start
    # i.e. no "start:X ... start:Y ... end:X" patterns
    starts = [e for e in call_log if e.startswith("start:")]
    ends = [e for e in call_log if e.startswith("end:")]
    assert len(starts) == 3
    assert len(ends) == 3

    # Verify interleaving didn't happen: for every start at position i,
    # the corresponding end must appear before the next start
    for i in range(0, len(call_log) - 1, 2):
        assert call_log[i].startswith("start:")
        assert call_log[i + 1].startswith("end:")
        start_key = call_log[i].split(":", 1)[1]
        end_key = call_log[i + 1].split(":", 1)[1]
        # The JSON payload text for start and end of the same call must match
        assert start_key == end_key, f"Interleaved sends detected: {call_log}"


# ---------------------------------------------------------------------------
# _send() disconnect: sets _disconnected flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_failure_sets_disconnected():
    """_send() must set _disconnected=True when send_text raises."""
    frontend, ws = _make_frontend(send_ok=False)
    # Override send_text to raise
    ws.send_text = AsyncMock(side_effect=Exception("broken pipe"))
    frontend._ws = ws

    await frontend._send({"type": "text", "content": "hi"})

    assert frontend._disconnected is True
