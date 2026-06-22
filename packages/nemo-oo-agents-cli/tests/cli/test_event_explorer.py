# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the terminal event explorer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from nemo_oo_agents_cli.tui.commands import EventsCommand
from nemo_oo_agents_cli.tui.event_explorer import (
    EventExplorerModel,
    EventExplorerRow,
    build_event_rows,
    detail_match_occurrences,
    highlighted_detail_lines,
    render_event_explorer,
    wrapped_detail_lines,
)
from nemo_oo_agents_cli.tui.output import TextOutput


class _FakeEvent:
    def __init__(self, event_type: str, **fields):
        self.event_type = event_type
        for key, value in fields.items():
            setattr(self, key, value)

    def model_dump(self):
        return {"event_type": self.event_type, **self.__dict__}


def test_event_explorer_builds_rows_and_full_text_searches() -> None:
    events = [
        ("1", _FakeEvent("TUIUserInput", text="please inspect frobnicator")),
        (
            "2",
            _FakeEvent(
                "ToolCallEvent",
                name="execute_python",
                arguments={"code": "# comment\nprint('needle')"},
            ),
        ),
        ("3", _FakeEvent("PythonOutput", execution_status="complete", stdout="needle output")),
    ]
    em = SimpleNamespace(items=lambda: events)

    rows = build_event_rows(em)
    model = EventExplorerModel(rows)

    assert [row.tag for row in rows] == ["1", "2", "3"]
    assert "execute_python" in rows[1].summary

    model.set_query("needle output")
    assert [rows[i].tag for i in model.matches] == ["3"]

    model.set_query("needle")
    assert [rows[i].tag for i in model.matches] == ["2", "3"]


def test_event_explorer_navigation_and_rendering() -> None:
    rows = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                ("1", _FakeEvent("TUIUserInput", text="first")),
                ("2", _FakeEvent("TUIUserInput", text="second")),
                ("3", _FakeEvent("TUIUserInput", text="third")),
            ]
        )
    )
    model = EventExplorerModel(rows)

    model.move(-1)
    assert model.current.tag == "2"
    model.move(+1)
    assert model.current.tag == "3"
    model.jump_home()
    assert model.current.tag == "1"

    rendered = render_event_explorer(model, width=70, height=14)
    assert "Event Explorer" in rendered
    assert "1" in rendered
    assert "↑/↓ matches/scroll" in rendered


def test_event_explorer_search_mode_up_down_moves_between_match_occurrences() -> None:
    row = build_event_rows(
        SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="alpha " * 80))])
    )[0]
    model = EventExplorerModel([row])
    model.edit_query("alpha")
    model.focus = "list"
    render_event_explorer(model, width=44, height=8)
    first_offset = model.detail_offset

    model.move_or_scroll(+1)
    render_event_explorer(model, width=44, height=8)

    assert model.current.tag == "1"
    assert model.search_line_cursor == 1
    assert model.detail_offset >= first_offset

    model.move_or_scroll(-1)
    render_event_explorer(model, width=44, height=8)
    assert model.current.tag == "1"
    assert model.search_line_cursor == 0


def test_event_explorer_fts_list_navigation_centers_current_match() -> None:
    row = build_event_rows(
        SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="alpha " * 120))])
    )[0]
    model = EventExplorerModel([row])
    model.edit_query("alpha")
    model.focus = "list"
    render_event_explorer(model, width=44, height=8)

    for _ in range(18):
        model.move_or_scroll(+1)
        render_event_explorer(model, width=44, height=8)

    target = model.current_search_line()
    assert target is not None
    assert model._last_detail_visible_lines > 0
    middle = model.detail_offset + model._last_detail_visible_lines // 2
    assert abs(target - middle) <= 1


def test_event_explorer_fts_detail_focus_up_down_scrolls_text() -> None:
    row = build_event_rows(
        SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="alpha " * 120))])
    )[0]
    model = EventExplorerModel([row])
    model.edit_query("alpha")
    model.focus = "detail"
    render_event_explorer(model, width=44, height=8)

    model.move_or_scroll(+1)
    rendered = render_event_explorer(model, width=90, height=8)

    assert model.search_active is True
    assert model.search_line_cursor == 0
    assert model.detail_offset == 1
    assert "↑/↓ scroll text" in rendered


def test_event_explorer_fts_current_match_uses_distinct_highlight() -> None:
    row = build_event_rows(
        SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="alpha beta alpha"))])
    )[0]
    line_no, occurrence_no = detail_match_occurrences(row, width=80, query="alpha")[0]
    lines = highlighted_detail_lines(
        row,
        width=80,
        query="alpha",
        current_match_line=line_no,
        current_match_occurrence=occurrence_no,
    )
    joined = "\n".join(lines)

    assert "\x1b[30;106malpha\x1b[0m" in joined
    assert "\x1b[30;43malpha\x1b[0m" in joined


def test_event_explorer_current_match_highlights_second_match_on_same_line() -> None:
    row = build_event_rows(
        SimpleNamespace(
            items=lambda: [("1", _FakeEvent("TUIUserInput", text="alpha beta alpha gamma"))]
        )
    )[0]
    occurrences = detail_match_occurrences(row, width=120, query="alpha")
    same_line = [(line, occurrence) for line, occurrence in occurrences if occurrence == 1]
    assert same_line
    line_no, occurrence_no = same_line[0]

    lines = highlighted_detail_lines(
        row,
        width=120,
        query="alpha",
        current_match_line=line_no,
        current_match_occurrence=occurrence_no,
    )
    selected_line = lines[line_no]

    assert selected_line.count("\x1b[30;106malpha\x1b[0m") == 1
    assert selected_line.count("\x1b[30;43malpha\x1b[0m") >= 1
    assert selected_line.index("\x1b[30;43malpha\x1b[0m") < selected_line.index(
        "\x1b[30;106malpha\x1b[0m"
    )


def test_event_explorer_ansi_styles_header_footer_and_mode_label() -> None:
    model = EventExplorerModel(
        build_event_rows(
            SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="alpha"))])
        )
    )
    model.edit_query("alpha")

    rendered = render_event_explorer(model, width=90, height=10, ansi=True)
    lines = rendered.splitlines()

    assert lines[0].startswith("\x1b[48;5;236;38;5;252m")
    assert lines[-1].startswith("\x1b[48;5;236;38;5;252m")
    assert "\x1b[1;30;45mFTS MODE\x1b[0m" in lines[-1]
    assert "\x1b[48;5;236;38;5;252m" in lines[-1].split("\x1b[1;30;45mFTS MODE\x1b[0m", 1)[1]

    model.search_active = False
    rendered = render_event_explorer(model, width=90, height=10, ansi=True)
    assert "\x1b[1;30;46mBROWSE MODE\x1b[0m" in rendered.splitlines()[-1]


def test_event_explorer_fts_mode_survives_tab_focus_changes() -> None:
    rows = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                ("1", _FakeEvent("TUIUserInput", text="alpha one")),
                ("2", _FakeEvent("TUIUserInput", text="alpha two")),
            ]
        )
    )
    model = EventExplorerModel(rows)
    model.edit_query("alpha")
    model.toggle_focus()
    assert model.search_active is True
    assert model.focus == "detail"

    rendered = render_event_explorer(model, width=90, height=12)
    assert "FTS MODE" in rendered
    assert "pane=event text" in rendered
    assert "↑/↓ scroll text" in rendered

    model.move_or_scroll(+1)
    assert model.search_active is True
    assert model.focus == "detail"
    assert model.current.tag == "1"

    model.toggle_focus()
    assert model.search_active is True
    assert model.focus == "list"
    rendered = render_event_explorer(model, width=90, height=12)
    assert "↑/↓ next match" in rendered
    model.move_or_scroll(+1)
    assert model.current.tag == "2"


def test_event_explorer_footer_does_not_advertise_noop_enter_in_browse_mode() -> None:
    model = EventExplorerModel([EventExplorerRow("1", "T", "summary", "summary", "detail")])

    browse = render_event_explorer(model, width=120, height=10)
    assert "enter browse" not in browse
    assert "enter exit FTS" not in browse

    model.search_active = True
    fts = render_event_explorer(model, width=120, height=10)
    assert "enter exit FTS" in fts


def test_event_explorer_advertises_q_close() -> None:
    model = EventExplorerModel(
        build_event_rows(
            SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="hello"))])
        )
    )

    rendered = render_event_explorer(model, width=120, height=10)
    source = Path(
        "packages/nemo-oo-agents-cli/src/nemo_oo_agents_cli/tui/event_explorer.py"
    ).read_text()

    assert "q quit" not in rendered
    assert "esc clear" in rendered
    assert "q close" in rendered
    assert '@kb.add("q")' not in source


def test_event_explorer_browse_mode_label_is_explicit() -> None:
    model = EventExplorerModel(
        build_event_rows(
            SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="hello"))])
        )
    )

    rendered = render_event_explorer(model, width=90, height=10)

    assert "BROWSE MODE" in rendered
    assert "pane=events" in rendered


def test_event_explorer_search_mode_moves_to_next_event_after_last_occurrence() -> None:
    rows = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                ("1", _FakeEvent("TUIUserInput", text="alpha one")),
                ("2", _FakeEvent("TUIUserInput", text="alpha two")),
            ]
        )
    )
    model = EventExplorerModel(rows)
    model.edit_query("alpha")
    render_event_explorer(model, width=60, height=10)

    model.move_or_scroll(+1)
    render_event_explorer(model, width=60, height=10)

    assert model.current.tag == "2"
    assert model.search_line_cursor == 0


def test_event_explorer_search_no_matches() -> None:
    model = EventExplorerModel(
        build_event_rows(
            SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="hello"))])
        )
    )
    model.set_query("missing")
    rendered = render_event_explorer(model, width=60, height=10)
    assert "No matches" in rendered


def test_event_explorer_search_shows_match_position_and_highlights_matches() -> None:
    rows = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                ("1", _FakeEvent("TUIUserInput", text="alpha one")),
                ("2", _FakeEvent("TUIUserInput", text="beta two")),
                ("3", _FakeEvent("TUIUserInput", text="alpha three")),
            ]
        )
    )
    model = EventExplorerModel(rows)
    model.set_query("alpha")

    rendered = render_event_explorer(model, width=80, height=14, ansi=True)
    assert "match 1/2" in rendered
    assert "[30;43malpha[0m" in rendered

    model.move(+1)
    rendered = render_event_explorer(model, width=80, height=14, ansi=True)
    assert "match 2/2" in rendered
    assert model.current.tag == "3"


def test_event_explorer_search_highlights_matches_inside_detail_text() -> None:
    row = build_event_rows(
        SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="find alpha here"))])
    )[0]
    joined = "\n".join(highlighted_detail_lines(row, width=80, query="alpha"))

    assert "\x1b[30;43malpha\x1b[0m" in joined


def test_event_explorer_wraps_long_event_details_and_scrolls_within_event() -> None:
    long_text = "alpha " * 40
    row = build_event_rows(
        SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text=long_text))])
    )[0]
    lines = wrapped_detail_lines(row, width=32)

    assert len(lines) > len(row.detail.splitlines())
    assert all(len(line) <= 32 for line in lines)

    model = EventExplorerModel([row])
    render_event_explorer(model, width=44, height=10)
    before = model.detail_offset
    model.page_detail(+4)
    assert model.detail_offset > before
    rendered = render_event_explorer(model, width=44, height=10)
    assert "event lines" in rendered


def test_event_explorer_highlights_execute_python_code() -> None:
    row = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                (
                    "2",
                    _FakeEvent(
                        "ToolCallEvent",
                        name="execute_python",
                        arguments={"code": "for i in range(3):\n    print(i)"},
                    ),
                )
            ]
        )
    )[0]

    lines = highlighted_detail_lines(row, width=50)
    joined = "\n".join(lines)
    assert "code (python):" in joined
    assert joined.index("code (python):") < joined.index("event:")
    assert "\x1b[" in joined
    assert "print" in joined


def test_event_explorer_extracts_fenced_code_blocks_to_top() -> None:
    row = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                (
                    "4",
                    _FakeEvent(
                        "AgentMessage",
                        content="before\n```python\nvalue = 42\nprint(value)\n```\nafter",
                    ),
                )
            ]
        )
    )[0]

    lines = wrapped_detail_lines(row, width=50)
    joined = "\n".join(lines)
    assert row.code == "value = 42\nprint(value)"
    assert lines[0] == "code (python):"
    assert joined.index("value = 42") < joined.index("event:")


def test_event_explorer_tab_focus_changes_up_down_semantics() -> None:
    long_text = "alpha " * 80
    rows = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                ("1", _FakeEvent("TUIUserInput", text="first")),
                ("2", _FakeEvent("TUIUserInput", text=long_text)),
            ]
        )
    )
    model = EventExplorerModel(rows)
    assert model.focus == "list"
    assert model.current.tag == "2"

    model.toggle_focus()
    assert model.focus == "detail"
    render_event_explorer(model, width=44, height=10)
    model.scroll_detail(+3)
    assert model.current.tag == "2"
    assert model.detail_offset > 0
    rendered = render_event_explorer(model, width=44, height=10)
    assert "BROWSE MODE" in rendered
    assert "pane=event text" in rendered
    assert "❯ event lines" in rendered

    model.toggle_focus()
    model.move(-1)
    assert model.current.tag == "1"


def test_event_explorer_highlights_python_repr_event() -> None:
    row = build_event_rows(
        SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="hello"))])
    )[0]
    joined = "\n".join(highlighted_detail_lines(row, width=70))
    plain = __import__("re").sub(r"\x1b\[[0-9;]*m", "", joined)

    assert "event:" in joined
    assert "\x1b[" in joined
    assert "TUIUserInput(" in plain
    assert "hello" in plain


def test_event_explorer_renders_tui_agent_message_as_markdown_only() -> None:
    row = build_event_rows(
        SimpleNamespace(
            items=lambda: [
                (
                    "5",
                    _FakeEvent(
                        "TUIAgentMessage",
                        content="# Answer\n\nThis is **markdown**, not repr.\n\n```python\nprint(1)\n```",
                    ),
                )
            ]
        )
    )[0]

    assert row.markdown is not None
    assert row.code is not None

    plain = "\n".join(wrapped_detail_lines(row, width=50))
    assert "# Answer" in plain
    assert "TUIAgentMessage(" not in plain
    assert "event:" not in plain

    highlighted = "\n".join(highlighted_detail_lines(row, width=50))
    stripped = __import__("re").sub(r"\x1b\[[0-9;]*m", "", highlighted)
    assert "Answer" in stripped
    assert "TUIAgentMessage(" not in stripped
    assert "event:" not in stripped


def test_event_explorer_handles_tiny_resize_heights_without_overflowing_body() -> None:
    model = EventExplorerModel(
        build_event_rows(
            SimpleNamespace(items=lambda: [("1", _FakeEvent("TUIUserInput", text="hello"))])
        )
    )

    rendered = render_event_explorer(model, width=40, height=3)

    assert len(rendered.splitlines()) == 3
    assert "Event Explorer" in rendered


@pytest.mark.asyncio
async def test_events_command_opens_in_app_explorer() -> None:
    agent = MagicMock()
    agent.event_manager = MagicMock()
    frontend = MagicMock()
    frontend.open_event_explorer = AsyncMock()
    config = MagicMock()

    cmd = EventsCommand(frontend, config, agent)
    result = await cmd.execute([])

    assert result.success is True
    assert isinstance(result.outputs[0], TextOutput)
    assert "closed" in result.outputs[0].content
    frontend.open_event_explorer.assert_awaited_once_with(agent.event_manager)


def test_events_command_rejects_args() -> None:
    cmd = EventsCommand(MagicMock(), MagicMock(), MagicMock())

    ok, error = cmd.validate_args(["1"])

    assert ok is False
    assert error == "Usage: /events"


@pytest.mark.asyncio
async def test_events_command_reports_failures() -> None:
    agent = MagicMock()
    agent.event_manager = MagicMock()
    frontend = MagicMock()
    frontend.open_event_explorer = AsyncMock(side_effect=RuntimeError("boom"))
    config = MagicMock()

    cmd = EventsCommand(frontend, config, agent)
    result = await cmd.execute([])

    assert result.success is False
    assert "boom" in result.outputs[0].content


@pytest.mark.asyncio
async def test_tui_app_opens_and_closes_event_explorer_in_app() -> None:
    from cli.tui_app_harness import FakeAgent, TUIHarness

    agent = FakeAgent()
    agent.event_manager = SimpleNamespace(
        items=lambda: [("1", _FakeEvent("TUIUserInput", text="alpha event"))]
    )
    async with TUIHarness(agent=agent) as h:
        task = asyncio.create_task(h.app.open_event_explorer(agent.event_manager))
        await h.wait_for(lambda: h.app._event_explorer_model is not None)

        assert h.app._event_explorer_model.current.tag == "1"
        await h.press("q")
        await asyncio.wait_for(task, timeout=1)
        assert h.app._event_explorer_model is None


@pytest.mark.asyncio
async def test_tui_app_event_explorer_keys_do_not_edit_prompt() -> None:
    from cli.tui_app_harness import FakeAgent, TUIHarness

    agent = FakeAgent()
    agent.event_manager = SimpleNamespace(
        items=lambda: [
            ("1", _FakeEvent("TUIUserInput", text="alpha one")),
            ("2", _FakeEvent("TUIUserInput", text="alpha two")),
        ]
    )
    async with TUIHarness(agent=agent) as h:
        task = asyncio.create_task(h.app.open_event_explorer(agent.event_manager))
        await h.wait_for(lambda: h.app._event_explorer_model is not None)

        await h.type_keys("/")
        await h.type_keys("alpha")
        await h.press("down")

        assert h.capture_input() == ""
        assert h.app._event_explorer_model.query == "alpha"
        assert h.app._event_explorer_model.search_active is True
        await h.press("escape")
        await h.wait_for(lambda: h.app._event_explorer_model.search_active is False)
        await h.press("escape")
        await h.wait_for(lambda: h.app._event_explorer_model.query == "")
        await h.press("q")
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_tui_app_event_explorer_fts_can_search_printable_navigation_and_quit_keys() -> None:
    from cli.tui_app_harness import FakeAgent, TUIHarness

    agent = FakeAgent()
    agent.event_manager = SimpleNamespace(
        items=lambda: [("1", _FakeEvent("TUIUserInput", text="jqk event"))]
    )
    async with TUIHarness(agent=agent) as h:
        task = asyncio.create_task(h.app.open_event_explorer(agent.event_manager))
        await h.wait_for(lambda: h.app._event_explorer_model is not None)

        await h.type_keys("/")
        await h.type_keys("jqk")

        await h.wait_for(lambda: h.app._event_explorer_model.query == "jqk")
        assert h.capture_input() == ""
        assert h.app.active_subview is not None

        await h.press("escape")
        await h.wait_for(lambda: h.app._event_explorer_model.search_active is False)
        await h.press("q")
        await asyncio.wait_for(task, timeout=1)


def test_event_explorer_has_in_app_mouse_scroll_bindings() -> None:
    source = Path(
        "packages/nemo-oo-agents-cli/src/nemo_oo_agents_cli/tui/tui_application.py"
    ).read_text()

    assert "open_event_explorer" in source
    assert "Keys.ScrollDown" in source
    assert "Keys.ScrollUp" in source
    assert "mouse_support=True" in source
    assert "_SuspendedPromptToolkitResize" not in source
