# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``AgentEventRenderer``.

The renderer owns three pieces of per-turn state (``_in_cell``,
``_pending_messages``, ``_agent_has_messaged``) and three event handlers
that transition them. These tests construct the renderer directly with a
real ``EventManager`` and a list-append ``emit_text`` so we can assert on
the exact sequence of emitted Rich renderables — no prompt_toolkit, no
block queue, no frontend.

Covers the gaps flagged by the post-refactor test-coverage review:
- buffered ``self.message()`` during a python cell flushes AFTER the
  cell completes, not in the middle of it
- ``reset_turn()`` clears leftover ``_in_cell`` state from a cancelled
  turn so the next turn isn't silently buffering messages forever
- prefill tool-call-ids short-circuit but still flush pending messages
- ``show_python=True`` path renders the full cell (oo python / oo stdout)
- ``detach()`` restores any prior ``_render_message`` hook
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from rich.markdown import Markdown
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from nemo_oo_agents_cli.tui.agent_event_renderer import AgentEventRenderer
from nemo_oo_agents_cli.tui.theme import COLORS

# ── helpers ────────────────────────────────────────────────────────────


class _FakeEventManager:
    """Records ``.on(type, handler)`` calls so we can drive the handlers
    directly — we don't need a real EventManager's dispatch path to
    unit-test the renderer's *reactions* to events."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event_type: str, handler: Any) -> Any:
        self.handlers.setdefault(event_type, []).append(handler)

        def _unsub() -> None:
            self.handlers.get(event_type, []).remove(handler)

        return _unsub

    def fire(self, event_type: str, event: Any) -> None:
        for h in self.handlers.get(event_type, []):
            h(event)


class _FakeAgent:
    """Minimal agent surface the renderer touches: event_manager + render hook."""

    def __init__(self) -> None:
        self.event_manager = _FakeEventManager()
        self._render_message: Any = None


def _mk(show_python: bool = False) -> tuple[_FakeAgent, list[Any], AgentEventRenderer]:
    agent = _FakeAgent()
    emitted: list[Any] = []
    pending_code: dict[str, str] = {}
    r = AgentEventRenderer(
        agent=agent,  # type: ignore[arg-type]
        emit_text=emitted.append,
        show_python=lambda: show_python,
        pending_code=pending_code,
        colors=COLORS,
    )
    return agent, emitted, r


def _fire_reasoning(em: _FakeEventManager, content: str) -> None:
    em.fire("Reasoning", SimpleNamespace(content=content))


def _fire_tool_call(em: _FakeEventManager, code: str, tool_call_id: str = "t1") -> None:
    em.fire(
        "ToolCallEvent",
        SimpleNamespace(
            name="execute_python",
            tool_call_id=tool_call_id,
            arguments={"code": code},
        ),
    )


def _fire_python_output(
    em: _FakeEventManager, stdout: str = "", stderr: str = "", tool_call_id: str = "t1"
) -> None:
    em.fire(
        "PythonOutput",
        SimpleNamespace(tool_call_id=tool_call_id, stdout=stdout, stderr=stderr),
    )


# ── tests ──────────────────────────────────────────────────────────────


def test_message_mid_cell_buffers_and_flushes_below_output() -> None:
    """``self.message()`` called between ToolCall and PythonOutput renders
    AFTER the cell, not in the middle of it. Pins the _in_cell buffering
    invariant that makes cells feel like notebook cells."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    _fire_tool_call(agent.event_manager, "print('hi')")
    # Agent called self.message() DURING the cell — via the render-hook.
    assert agent._render_message is not None
    agent._render_message("mid-cell note")
    _fire_python_output(agent.event_manager, stderr="warn")

    # Order: code preview (∴ line), stderr line (│ warn), then the
    # post-cell markdown (OO rule + Markdown). The message MUST come
    # after the stderr line, never before.
    kinds = [type(e).__name__ for e in emitted]
    assert kinds.count("Text") >= 2  # preview + stderr line
    assert any(isinstance(e, Markdown) for e in emitted)
    markdown_idx = next(i for i, e in enumerate(emitted) if isinstance(e, Markdown))
    stderr_idx = next(i for i, e in enumerate(emitted) if isinstance(e, Text) and "warn" in str(e))
    assert stderr_idx < markdown_idx, (
        "message must render AFTER stderr, not interleaved with the cell"
    )


def test_reset_turn_clears_in_cell_from_cancelled_prior_turn() -> None:
    """If the previous turn was cancelled mid-cell (ToolCallEvent fired
    but no PythonOutput), ``_in_cell`` stays True and further
    ``self.message()`` calls silently buffer. ``reset_turn()`` on the
    next user message must clear that state."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    # Simulate a cancelled cell: ToolCall fires, no PythonOutput, then
    # the agent emits a trailing message that gets buffered.
    _fire_tool_call(agent.event_manager, "x = 1")
    assert r._in_cell is True
    agent._render_message("stranded")
    assert r._pending_messages == ["stranded"]

    # New turn starts. reset_turn must flush stragglers AND clear
    # _in_cell so the next message() renders inline instead of
    # buffering forever.
    emitted_before_reset = list(emitted)
    r.reset_turn()
    assert r._in_cell is False
    assert r._pending_messages == []
    # The stranded message was flushed (not lost).
    assert any(isinstance(e, Markdown) for e in emitted[len(emitted_before_reset) :])

    # Now a fresh message should render inline.
    before = len(emitted)
    agent._render_message("fresh")
    # At least one new Markdown block was emitted immediately.
    new = emitted[before:]
    assert any(isinstance(e, Markdown) for e in new)


def test_prefill_tool_call_does_not_render_code_but_does_flush() -> None:
    """Prefill executions (internal inspection, e.g. ``prefill_abc``) show
    a friendly message instead of code — and the matching PythonOutput
    short-circuits the full cell render while still flushing any
    pending messages."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    _fire_tool_call(agent.event_manager, "inspect(x)", tool_call_id="prefill_a")
    agent._render_message("post-inspect")
    _fire_python_output(agent.event_manager, stdout="noise", tool_call_id="prefill_a")

    # Preview line says "Inspecting inputs..."
    assert any(isinstance(e, Text) and "Inspecting inputs" in str(e) for e in emitted)
    # The stdout from the prefill should NOT render — it's internal noise.
    assert not any(isinstance(e, Text) and "noise" in str(e) for e in emitted)
    # The buffered message WAS flushed.
    assert any(isinstance(e, Markdown) for e in emitted)


def test_show_python_true_renders_full_cell() -> None:
    """With show_python=True, PythonOutput emits a notebook-style cell:
    'oo python' rule, Syntax(code), 'oo stdout' rule, stdout Text."""
    agent, emitted, r = _mk(show_python=True)
    r.attach()

    _fire_tool_call(agent.event_manager, "print('ok')")
    _fire_python_output(agent.event_manager, stdout="ok\n")

    # Exactly one Syntax renderable for the code.
    syntaxes = [e for e in emitted if isinstance(e, Syntax)]
    assert len(syntaxes) == 1

    # 'oo python' rule precedes syntax; 'oo stdout' rule precedes stdout.
    rules = [e for e in emitted if isinstance(e, Rule)]
    titles = [str(r.title) if r.title is not None else "" for r in rules]
    assert any("python" in t for t in titles)
    assert any("stdout" in t for t in titles)


def test_detach_restores_prior_render_message_hook() -> None:
    """``attach`` chains onto any prior ``_render_message`` so observers
    (e.g. a web mirror) still fire. ``detach`` restores the prior hook
    verbatim so a post-shutdown ``agent.message()`` doesn't invoke a
    dead renderer."""
    agent, emitted, r = _mk(show_python=False)

    observer_calls: list[str] = []
    agent._render_message = observer_calls.append

    r.attach()
    assert agent._render_message is not observer_calls.append  # chained

    # A message fires BOTH the observer AND the renderer.
    agent._render_message("hi")
    assert observer_calls == ["hi"]
    assert any(isinstance(e, Markdown) for e in emitted)

    r.detach()
    # Prior hook restored — observer still works, renderer does not fire.
    emitted.clear()
    observer_calls.clear()
    agent._render_message("post-shutdown")
    assert observer_calls == ["post-shutdown"]
    assert emitted == []  # renderer is gone


def test_reasoning_event_emits_dim_italic_text() -> None:
    """``Reasoning`` content is emitted as a dim italic ``Text`` block."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    _fire_reasoning(agent.event_manager, "thinking...")

    texts = [e for e in emitted if isinstance(e, Text)]
    assert len(texts) == 1
    assert "thinking" in str(texts[0])


def test_empty_reasoning_content_is_ignored() -> None:
    """Blank reasoning events don't emit anything."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    _fire_reasoning(agent.event_manager, "")
    _fire_reasoning(agent.event_manager, "   \n ")

    assert emitted == []


def test_reset_turn_no_buffered_messages_is_noop() -> None:
    """``reset_turn`` with an empty buffer just clears flags; doesn't
    emit a stray ``OO ─`` rule."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    # First turn emits one message.
    agent._render_message("turn 1")
    assert any(isinstance(e, Rule) for e in emitted)  # OO rule fires once
    emitted.clear()

    # reset_turn with nothing pending.
    r.reset_turn()
    assert emitted == []  # no stray output
    assert r._agent_has_messaged is False


def test_attach_is_idempotent() -> None:
    """A second ``attach`` call while already attached is a no-op —
    handlers aren't double-subscribed."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()
    r.attach()  # second call

    _fire_reasoning(agent.event_manager, "x")

    texts = [e for e in emitted if isinstance(e, Text)]
    assert len(texts) == 1  # not 2


@pytest.mark.parametrize(
    "code,expected_fragment",
    [
        ("x = 1", "x = 1"),
        ("# explain what this does\nrun_it()", "explain"),
    ],
)
def test_code_preview_first_line_shape(code: str, expected_fragment: str) -> None:
    """The ∴ code-preview line includes the first (comment or code) line."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    _fire_tool_call(agent.event_manager, code)

    preview_texts = [e for e in emitted if isinstance(e, Text) and "∴" in str(e)]
    assert preview_texts, "expected a ∴ preview line"
    assert expected_fragment in str(preview_texts[0])
