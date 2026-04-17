# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test harness for driving a ``TUIApplication`` in unit tests.

Usage::

    async with TUIHarness() as h:
        await h.type_keys("hello")
        await h.press("enter")
        assert "hello" in h.capture_output()

The harness owns:

- a ``PipeInput`` — bytes written with ``send_text()`` look like keystrokes
- a ``DummyOutput`` — swallows rendering but the app still thinks it has a TTY
- a background task running ``app.run_async()``
- a scriptable ``FakeAgent`` that yields controllable responses

Assertions read the app's *logical* state (input/output buffer text, queue
messages, status line) rather than parsed terminal output — terminal-level
correctness is covered by manual smoke, and tying unit tests to rendered
ANSI bytes is brittle.

Timing model: writing to the pipe is synchronous, but prompt_toolkit
parses input on the event loop. Every driver method ends with an
``asyncio.sleep(0)`` (or an explicit ``wait_for``) so the event loop has
a chance to drain the pipe before the next assertion runs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

if TYPE_CHECKING:
    from nemo_oo_agents_cli.tui.tui_application import TUIApplication


# ── key-name → terminal escape-sequence --------------------------------------

# Only what the tests actually press. Extend as new tests demand it.
_KEY_SEQUENCES: dict[str, str] = {
    "enter": "\r",
    "escape": "\x1b",
    "tab": "\t",
    "backspace": "\x7f",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "c-c": "\x03",
    "c-d": "\x04",
    "c-j": "\n",  # bare LF — used by prompt_toolkit as "Shift+Enter"
    "s-enter": "\x1b\r",  # Alt+Enter / Esc+Enter — prompt_toolkit treats as newline
}


def _key_sequence(key: str) -> str:
    try:
        return _KEY_SEQUENCES[key.lower()]
    except KeyError:
        raise ValueError(
            f"Unknown key {key!r}. Add it to _KEY_SEQUENCES in tui_app_harness."
        ) from None


# ── scriptable agent mock ----------------------------------------------------


class FakeAgent:
    """Scriptable stand-in for ``TUIAgent`` used by harness tests.

    Each call to ``respond(user_message)`` pops the next scripted response
    off ``self.script`` and invokes it with ``self`` so the script can
    emit activity, messages, or simulate work. If the script is empty the
    agent just waits on ``self.block`` — useful for testing "agent working"
    states where the test controls when the agent finishes.
    """

    def __init__(self) -> None:
        self.script: list[Callable[[FakeAgent, str], Any]] = []
        self.messages_received: list[str] = []
        self.block = asyncio.Event()
        self.block.set()  # default: respond returns immediately
        self.emit: Callable[[str], None] | None = None  # set by app

    def emit_message(self, text: str) -> None:
        """Render ``text`` as Markdown → ANSI and push to the output buffer.

        Mirrors what the real frontend does with ``AgentMessage`` objects
        (Rich Markdown renderer), so tests that assert on ANSI presence
        exercise the same rendering path as production.
        """
        if self.emit is None:
            return
        import io as _io

        from rich.console import Console
        from rich.markdown import Markdown

        buf = _io.StringIO()
        Console(
            file=buf, force_terminal=True, color_system="256", width=80, legacy_windows=False
        ).print(Markdown(text))
        self.emit(buf.getvalue())

    async def respond(self, user_message: str) -> Any:
        self.messages_received.append(user_message)
        if self.script:
            step = self.script.pop(0)
            return await _maybe_await(step(self, user_message))
        await self.block.wait()
        return None

    def queue(self, step: Callable[[FakeAgent, str], Any]) -> None:
        """Add one scripted step to the end of the response sequence."""
        self.script.append(step)


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


# ── the harness itself -------------------------------------------------------


class TUIHarness(AbstractAsyncContextManager["TUIHarness"]):
    """Drive a ``TUIApplication`` from tests.

    Enters as an async context manager: starts the app's event loop task
    on ``__aenter__``, tears it down (with a timeout) on ``__aexit__``.
    """

    def __init__(self, agent: FakeAgent | None = None) -> None:
        self.agent = agent or FakeAgent()
        self._pipe_ctx: Any = None
        self._session_ctx: Any = None
        self._run_task: asyncio.Task | None = None
        self.app: TUIApplication | None = None

    async def __aenter__(self) -> TUIHarness:
        self._pipe_ctx = create_pipe_input()
        pipe = self._pipe_ctx.__enter__()
        self._session_ctx = create_app_session(input=pipe, output=DummyOutput())
        self._session_ctx.__enter__()

        # Import locally so the stub can evolve without breaking harness
        # consumers that don't import it.
        from nemo_oo_agents_cli.tui.tui_application import TUIApplication

        self.app = TUIApplication(agent=self.agent)
        self._pipe = pipe

        self._run_task = asyncio.create_task(self.app.run_async())
        # Let the app install its input reader before the test sends keys.
        await self._wait_for_app_ready()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            # Cancel any pending agent task so FakeAgent.respond() awaiting
            # on agent.block doesn't get orphaned at teardown.
            if self.app is not None:
                agent_task = getattr(self.app, "_agent_task", None)
                if agent_task is not None and not agent_task.done():
                    agent_task.cancel()
                    try:
                        await agent_task
                    except (asyncio.CancelledError, BaseException):
                        pass
                self.app.exit()
            if self._run_task is not None:
                try:
                    await asyncio.wait_for(self._run_task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    self._run_task.cancel()
        finally:
            if self._session_ctx is not None:
                self._session_ctx.__exit__(None, None, None)
            if self._pipe_ctx is not None:
                self._pipe_ctx.__exit__(None, None, None)

    async def _wait_for_app_ready(self) -> None:
        """Spin until the underlying Application reports ready."""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            app = self.app
            if app is not None and app.is_running:
                return
            await asyncio.sleep(0.01)
        raise RuntimeError("TUIApplication did not start within 2s")

    # ── input driving --------------------------------------------------

    async def type_keys(self, text: str) -> None:
        """Send literal characters as if the user typed them."""
        self._pipe.send_text(text)
        await asyncio.sleep(0)

    async def press(self, key: str) -> None:
        """Press a named key (``"enter"``, ``"up"``, ``"c-c"``, …)."""
        self._pipe.send_text(_key_sequence(key))
        await asyncio.sleep(0)

    async def submit_async(self, text: str) -> None:
        """Type ``text`` and press Enter. Doesn't wait for any side-effect."""
        await self.type_keys(text)
        await self.press("enter")

    # ── state-polling helpers -----------------------------------------

    async def wait_for(
        self, predicate: Callable[[], bool], timeout: float = 2.0, interval: float = 0.01
    ) -> None:
        """Yield control until ``predicate()`` returns truthy, or raise."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(interval)
        raise AssertionError(
            f"Predicate never became true within {timeout}s.\n"
            f"  input={self.capture_input()!r}\n"
            f"  output (tail)={self.capture_output()[-200:]!r}\n"
            f"  queued={self.capture_queued()!r}\n"
            f"  status={self.capture_status()!r}"
        )

    async def wait_input_equals(self, expected: str, timeout: float = 1.0) -> None:
        await self.wait_for(lambda: self.capture_input() == expected, timeout=timeout)

    async def wait_output_contains(self, needle: str, timeout: float = 1.0) -> None:
        await self.wait_for(lambda: needle in self.capture_output(), timeout=timeout)

    # ── introspection --------------------------------------------------

    def capture_input(self) -> str:
        assert self.app is not None
        return self.app.input_buffer.text

    def capture_output(self) -> str:
        assert self.app is not None
        return self.app.output_buffer.text

    def capture_output_ansi(self) -> str:
        """Raw (ANSI-bearing) output. ``capture_output`` strips ANSI; this
        preserves it so ``\\x1b[`` round-trip tests can assert styling."""
        assert self.app is not None
        return "".join(self.app._output_ansi)

    def capture_queued(self) -> list[str]:
        assert self.app is not None
        return list(self.app.state.messages) + list(self.app.state.commands)

    def capture_status(self) -> str:
        assert self.app is not None
        return self.app.status_text()
