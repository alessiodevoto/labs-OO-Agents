# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``AgentEventRenderer``.

The renderer owns two pieces of per-turn state (``_pending_messages``,
``_agent_has_messaged``) and three event handlers. These tests construct
the renderer directly with a ``_FakeAgent`` and a list-append
``emit_text`` so we can assert on the exact sequence of emitted Rich
renderables — no prompt_toolkit, no block queue, no frontend.

Covers the gaps flagged by the post-refactor test-coverage review:
- ``self.message()`` always buffers and flushes AFTER the nearest
  ``PythonOutput`` so messages land BELOW the code-preview + output
  block for the cell above them — even the first message of a turn
- ``reset_turn()`` flushes stragglers from a turn that ended after the
  last cell, so they're never silently dropped
- prefill tool-call-ids short-circuit the full-cell render but still
  flush pending messages
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


def _fire_summary(
    em: _FakeEventManager,
    *,
    summary_tag: str = "1..10",
    replaced_range: tuple[int, int] = (1, 10),
    children_tags: list[str] | None = None,
    summary_text: str | None = "Summary of events.",
) -> None:
    em.fire(
        "Summary",
        SimpleNamespace(
            summary_tag=summary_tag,
            replaced_range=replaced_range,
            children_tags=children_tags
            if children_tags is not None
            else [str(i) for i in range(1, 11)],
            summary_text=summary_text,
        ),
    )


# ── tests ──────────────────────────────────────────────────────────────


def test_message_mid_cell_buffers_and_flushes_below_output() -> None:
    """``self.message()`` called between ToolCall and PythonOutput renders
    AFTER the cell, not in the middle of it. Pins the buffering
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


def test_reset_turn_flushes_stragglers_from_previous_turn() -> None:
    """Messages emitted after the last ``PythonOutput`` of a turn (or
    during a turn that was cancelled mid-cell) stay in the buffer.
    ``reset_turn()`` on the next user submission must flush them
    visibly so they're never silently dropped."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    # Simulate a turn that emitted a message after its last cell (or
    # a cancelled-mid-cell turn) — message stays buffered.
    agent._render_message("stranded")
    assert r._pending_messages == ["stranded"]
    assert not any(isinstance(e, Markdown) for e in emitted)

    # Next user submission: reset_turn flushes the straggler.
    r.reset_turn()
    assert r._pending_messages == []
    assert any(isinstance(e, Markdown) for e in emitted)


def test_first_message_of_turn_never_emits_before_code_preview() -> None:
    """Regression pin: the FIRST ``self.message()`` of a turn — even
    when called AFTER the CodeAct prefill flushed — must NOT emit
    before a subsequent code preview. Every message is buffered until
    the next ``PythonOutput`` flushes it.

    Historically a short-lived refactor had `_render_message` emit
    immediately when no cell was "open", which let the first post-prefill
    message appear ABOVE the first real code preview. This test pins the
    opposite order: all messages land below the cell above them."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    # Turn 1: CodeAct prefill runs first (framework does this every turn).
    _fire_tool_call(agent.event_manager, "bind_self()", tool_call_id="prefill_t1")
    _fire_python_output(agent.event_manager, stdout="", tool_call_id="prefill_t1")

    # Now the agent generates its response: one self.message() call,
    # THEN a real code cell with its own message inside.
    agent._render_message("opening remark")
    # Snapshot emission count before the real code cell — the opening
    # remark must NOT have landed yet.
    before_real_cell = len(emitted)
    _fire_tool_call(agent.event_manager, "do_it()", tool_call_id="t2")
    agent._render_message("mid-cell detail")
    _fire_python_output(agent.event_manager, stdout="done", tool_call_id="t2")

    # No Markdown emitted between the prefill flush and the real
    # code-preview firing — the opening-remark message was buffered.
    pre_preview_slice = emitted[: before_real_cell + 1]  # +1 to include the preview
    markdowns_before_real = [e for e in pre_preview_slice if isinstance(e, Markdown)]
    assert markdowns_before_real == [], (
        "message leaked above the code preview — the first-message regression returned"
    )

    # Both messages land AFTER the real code preview (they're in the
    # tail emitted by _on_python_output's flush).
    real_preview_idx = next(
        i for i, e in enumerate(emitted) if isinstance(e, Text) and "do_it" in str(e)
    )
    markdowns = [i for i, e in enumerate(emitted) if isinstance(e, Markdown)]
    assert len(markdowns) == 2
    for m in markdowns:
        assert m > real_preview_idx


def test_return_result_tool_call_flushes_pending_messages() -> None:
    """When ``return_result`` fires as its own tool call (not inline in
    ``execute_python``), no ``PythonOutput`` event follows — so the
    turn-end flush used to be skipped and ``self.message()`` calls
    bufferered before the stop were silently dropped.

    Regression guard: the renderer must flush pending messages on
    ``return_result`` tool calls so the user sees the agent's last words.
    """
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    # Prior cell ran and flushed (e.g. the agent wrote a message then
    # issued return_result without wrapping it in execute_python).
    _fire_tool_call(agent.event_manager, "print('work')", tool_call_id="t1")
    _fire_python_output(agent.event_manager, stdout="work", tool_call_id="t1")

    # Agent calls self.message() from outside a code cell (e.g. inside
    # respond() after the last execute_python), then the LLM issues
    # return_result as a separate tool.
    agent._render_message("Final word")
    agent.event_manager.fire(
        "ToolCallEvent",
        SimpleNamespace(name="return_result", tool_call_id="rr1", arguments={"result": {}}),
    )

    # "Final word" should have rendered as Markdown.
    assert any(isinstance(e, Markdown) and "Final word" in str(e.markup) for e in emitted), (
        "return_result tool call must flush pending self.message() calls"
    )
    # Nothing remains buffered.
    assert r._pending_messages == []


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

    # A message fires the observer AND queues on the renderer.
    agent._render_message("hi")
    assert observer_calls == ["hi"]
    assert r._pending_messages == ["hi"]

    r.detach()
    # Prior hook restored — observer still works, renderer does not
    # queue (any more messages go only to the observer).
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

    # First turn: one message buffers, then flushes via PythonOutput.
    agent._render_message("turn 1")
    _fire_tool_call(agent.event_manager, "x=1", tool_call_id="t1")
    _fire_python_output(agent.event_manager, tool_call_id="t1")
    assert any(isinstance(e, Rule) for e in emitted)  # OO rule fires once
    assert r._pending_messages == []  # flushed
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


def test_summary_event_emits_dim_preview_line() -> None:
    """Applied summaries surface as ``∴ summarized <tag> · N events → NB
    summary`` so the user can see each collapse live.

    Regression guard: if Summary events stop being emitted by
    ``event_manager.collapse``, the TUI silently loses visibility into the
    summarizer (which is how a pathological cascade bug went undiagnosed
    until a DB audit).
    """
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    _fire_summary(
        agent.event_manager,
        summary_tag="1..600",
        replaced_range=(1, 600),
        children_tags=[str(i) for i in range(1, 601)],
        summary_text="x" * 2501,
    )

    texts = [e for e in emitted if isinstance(e, Text) and "∴" in str(e)]
    assert len(texts) == 1
    line = str(texts[0])
    assert "summarized" in line
    assert "1..600" in line
    assert "600 events" in line
    # 2501 chars → "2.4KB" (2501/1024 ≈ 2.4)
    assert "KB" in line


def test_summary_event_without_text_shows_truncation() -> None:
    """A collapse with ``summary_text=None`` is a truncation — shown with
    ``truncated`` and a ``(no summary)`` tail so it's visually distinct
    from a real summarization."""
    agent, emitted, r = _mk(show_python=False)
    r.attach()

    _fire_summary(
        agent.event_manager,
        summary_tag="1..50",
        replaced_range=(1, 50),
        children_tags=[str(i) for i in range(1, 51)],
        summary_text=None,
    )

    texts = [e for e in emitted if isinstance(e, Text) and "∴" in str(e)]
    assert len(texts) == 1
    line = str(texts[0])
    assert "truncated" in line
    assert "1..50" in line
    assert "no summary" in line


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
