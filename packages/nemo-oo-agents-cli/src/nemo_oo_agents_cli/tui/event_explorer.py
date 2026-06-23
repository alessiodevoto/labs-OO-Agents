# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Event explorer model, renderer, and in-app subview."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any

from .subapp import SubviewKeyResult


@dataclass
class EventExplorerRow:
    tag: str
    event_type: str
    summary: str
    search_text: str
    detail: str
    code: str | None = None
    code_language: str = "python"
    markdown: str | None = None


class EventExplorerModel:
    """Searchable, keyboard-navigable event list."""

    def __init__(self, rows: list[EventExplorerRow]) -> None:
        self.rows = rows
        self.query = ""
        self.matches = list(range(len(rows)))
        self.cursor = max(len(self.matches) - 1, 0)
        self.detail_offset = 0
        self.focus = "list"
        self.search_active = False
        self.search_line_cursor = 0
        self._last_detail_line_count = 0
        self._last_detail_match_lines: list[int] = []
        self._last_detail_match_occurrences: list[tuple[int, int]] = []
        self._last_detail_visible_lines = 0

    @property
    def empty(self) -> bool:
        return not self.rows

    @property
    def current_index(self) -> int | None:
        if not self.matches:
            return None
        self.cursor = min(max(self.cursor, 0), len(self.matches) - 1)
        return self.matches[self.cursor]

    @property
    def current(self) -> EventExplorerRow | None:
        idx = self.current_index
        return None if idx is None else self.rows[idx]

    def set_query(self, query: str) -> None:
        self.query = query
        words = [w.lower() for w in query.split() if w.strip()]
        if not words:
            self.matches = list(range(len(self.rows)))
        else:
            self.matches = [
                i
                for i, row in enumerate(self.rows)
                if all(word in row.search_text.lower() for word in words)
            ]
        self.cursor = 0
        self.detail_offset = 0
        self.search_line_cursor = 0

    def edit_query(self, text: str) -> None:
        self.set_query(text)
        self.search_active = True

    def move(self, delta: int) -> None:
        if not self.matches:
            return
        self.cursor = min(max(self.cursor + delta, 0), len(self.matches) - 1)
        self.detail_offset = 0
        self.search_line_cursor = 0

    def current_search_line(self) -> int | None:
        if self._last_detail_match_occurrences:
            match_count = len(self._last_detail_match_occurrences)
            self.search_line_cursor = min(max(self.search_line_cursor, 0), match_count - 1)
            return self._last_detail_match_occurrences[self.search_line_cursor][0]
        if self._last_detail_match_lines:
            match_count = len(self._last_detail_match_lines)
            self.search_line_cursor = min(max(self.search_line_cursor, 0), match_count - 1)
            return self._last_detail_match_lines[self.search_line_cursor]
        return None

    def center_detail_on_line(self, line: int, visible_lines: int | None = None) -> None:
        visible = max(
            visible_lines if visible_lines is not None else self._last_detail_visible_lines, 1
        )
        self.detail_offset = max(line - visible // 2, 0)
        self.clamp_detail_offset(visible)

    def move_search_occurrence(self, delta: int) -> None:
        if not self.matches:
            return
        match_count = len(self._last_detail_match_occurrences) or len(self._last_detail_match_lines)
        if not match_count:
            self.move(delta)
            return
        next_cursor = self.search_line_cursor + delta
        if 0 <= next_cursor < match_count:
            self.search_line_cursor = next_cursor
            target = self.current_search_line()
            if target is not None:
                self.center_detail_on_line(target)
            return
        old_cursor = self.cursor
        self.move(delta)
        if self.cursor != old_cursor:
            self.search_line_cursor = 0 if delta > 0 else 10**9

    def move_or_scroll(self, delta: int) -> None:
        if self.search_active and self.focus == "list":
            self.move_search_occurrence(delta)
        elif self.focus == "list":
            self.move(delta)
        else:
            self.scroll_detail(delta)

    def jump_home(self) -> None:
        self.cursor = 0
        self.detail_offset = 0
        self.search_line_cursor = 0

    def jump_end(self) -> None:
        if self.matches:
            self.cursor = len(self.matches) - 1
            self.detail_offset = 0
            self.search_line_cursor = 0

    def toggle_focus(self) -> None:
        self.focus = "detail" if self.focus == "list" else "list"

    def scroll_detail(self, delta: int) -> None:
        max_offset = max(self._last_detail_line_count - 1, 0)
        self.detail_offset = min(max(self.detail_offset + delta, 0), max_offset)

    def page_detail(self, delta: int) -> None:
        self.scroll_detail(delta)

    def clamp_detail_offset(self, visible_lines: int) -> None:
        max_offset = max(self._last_detail_line_count - max(visible_lines, 1), 0)
        self.detail_offset = min(max(self.detail_offset, 0), max_offset)


class EventExplorerView:
    """In-app subview wrapper for browsing recorded events."""

    title = "events"

    def __init__(self, event_manager: Any) -> None:
        self.model = EventExplorerModel(build_event_rows(event_manager))

    def render(self, width: int, height: int) -> str:
        return render_event_explorer(self.model, width, height, ansi=True)

    def handle_key(self, action: str, value: str = "") -> SubviewKeyResult:
        model = self.model
        if action == "quit":
            if model.search_active:
                model.edit_query(model.query + "q")
                return "handled"
            return "close"
        if action == "resume":
            if model.search_active:
                model.edit_query(model.query + "r")
                return "handled"
            return "ignored"
        if action == "escape":
            if model.search_active:
                model.search_active = False
            elif model.query:
                model.set_query("")
            else:
                return "ignored"
        elif action == "enter":
            model.search_active = False
        elif action == "slash":
            model.search_active = True
        elif action == "backspace":
            if model.search_active:
                model.edit_query(model.query[:-1])
        elif action == "tab":
            model.toggle_focus()
        elif action == "down":
            model.move_or_scroll(+1)
        elif action == "j":
            if model.search_active:
                model.edit_query(model.query + "j")
            else:
                model.move_or_scroll(+1)
        elif action == "up":
            model.move_or_scroll(-1)
        elif action == "k":
            if model.search_active:
                model.edit_query(model.query + "k")
            else:
                model.move_or_scroll(-1)
        elif action == "page_down":
            if not model.search_active:
                model.page_detail(+10)
        elif action == "page_up":
            if not model.search_active:
                model.page_detail(-10)
        elif action == "home":
            if not model.search_active:
                model.jump_home()
        elif action == "end":
            if not model.search_active:
                model.jump_end()
        elif action == "scroll_down":
            model.focus = "detail"
            model.scroll_detail(+3)
        elif action == "scroll_up":
            model.focus = "detail"
            model.scroll_detail(-3)
        elif action == "text":
            if model.search_active and value and value.isprintable():
                model.edit_query(model.query + value)
            else:
                return "ignored"
        else:
            return "ignored"
        return "handled"

    def on_open(self) -> None:
        pass

    def on_close(self) -> None:
        pass


def _event_to_mapping(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        try:
            return event.model_dump()
        except Exception:
            pass
    if hasattr(event, "dict"):
        try:
            return event.dict()
        except Exception:
            pass
    return {"repr": repr(event)}


def _event_summary(event: Any, event_type: str) -> str:
    if event_type == "ToolCallEvent":
        name = str(getattr(event, "name", "?"))
        args = getattr(event, "arguments", {})
        if name == "execute_python" and isinstance(args, dict):
            code = str(args.get("code", ""))
            for line in code.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return f"{name} — {stripped[:90]}"
        return name
    if event_type == "PythonOutput":
        status = str(getattr(event, "execution_status", "?")).rsplit(".", 1)[-1]
        stdout = str(getattr(event, "stdout", "") or "").strip()
        error = str(getattr(event, "error", "") or "").strip()
        if error:
            return f"{status} — {error.splitlines()[-1][:90]}"
        if stdout:
            return f"{status} — {stdout.splitlines()[0][:90]}"
        return status
    if event_type == "TUIUserInput":
        return str(getattr(event, "text", "") or "")[:100]
    if event_type == "Task":
        prompt = str(getattr(event, "prompt", "") or "").strip()
        return prompt.splitlines()[0][:100] if prompt else ""
    data = _event_to_mapping(event)
    for key in ("content", "text", "message", "name", "summary"):
        value = data.get(key)
        if value:
            return str(value).strip().splitlines()[0][:100]
    return repr(event)[:100]


def _extract_fenced_code(text: str) -> tuple[str, str] | None:
    import re

    match = re.search(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", text, re.DOTALL)
    if not match:
        return None
    language = match.group(1).strip() or "python"
    return match.group(2).rstrip("\n"), language


def _event_code(event: Any, event_type: str) -> tuple[str | None, str]:
    if event_type == "ToolCallEvent" and getattr(event, "name", None) == "execute_python":
        args = getattr(event, "arguments", {})
        if isinstance(args, dict) and args.get("code"):
            return str(args["code"]), "python"
    for value in _event_to_mapping(event).values():
        if isinstance(value, str):
            extracted = _extract_fenced_code(value)
            if extracted is not None:
                return extracted
    return None, "python"


def _format_detail(tag: str, event: Any) -> str:
    event_type = str(getattr(event, "event_type", type(event).__name__))
    data = _event_to_mapping(event)
    fields = [(k, v) for k, v in data.items() if k != "event_type"]
    if fields:
        body = f"{event_type}(" + ", ".join(f"{k}={v!r}" for k, v in fields) + ")"
    else:
        body = repr(event)
    return f"tag = {tag!r}\ntype = {event_type!r}\n\nevent = {body}"


def _event_markdown(event: Any, event_type: str) -> str | None:
    if event_type != "TUIAgentMessage":
        return None
    content = getattr(event, "content", None)
    if content is None:
        content = _event_to_mapping(event).get("content")
    return str(content) if content else ""


def build_event_rows(event_manager: Any) -> list[EventExplorerRow]:
    """Build explorer rows from an EventManager-like object."""
    rows: list[EventExplorerRow] = []
    if hasattr(event_manager, "items"):
        items = list(event_manager.items())
    else:
        items = [(tag, event_manager.get(tag)) for tag in event_manager.keys()]
    for tag, event in items:
        if event is None:
            continue
        event_type = str(getattr(event, "event_type", type(event).__name__))
        summary = _event_summary(event, event_type)
        detail = _format_detail(str(tag), event)
        code, code_language = _event_code(event, event_type)
        markdown = _event_markdown(event, event_type)
        search_text = f"{tag} {event_type} {summary} {detail} {code or ''} {markdown or ''}"
        rows.append(
            EventExplorerRow(
                tag=str(tag),
                event_type=event_type,
                summary=summary,
                search_text=search_text,
                detail=detail,
                code=code,
                code_language=code_language,
                markdown=markdown,
            )
        )
    return rows


def _wrap_plain_line(line: str, width: int) -> list[str]:
    if line == "":
        return [""]
    indent_len = len(line) - len(line.lstrip(" "))
    subsequent = " " * min(indent_len, max(width - 1, 0))
    return textwrap.wrap(
        line,
        width=max(width, 1),
        replace_whitespace=False,
        drop_whitespace=False,
        break_long_words=True,
        break_on_hyphens=False,
        subsequent_indent=subsequent,
    ) or [""]


def _search_terms(query: str) -> list[str]:
    return [term for term in query.split() if term.strip()]


def _highlight_terms(text: str, terms: list[str]) -> str:
    """ANSI-highlight query terms in plain text."""
    if not terms:
        return text
    import re

    pattern = re.compile(
        "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)), re.IGNORECASE
    )
    return pattern.sub(lambda m: f"\x1b[30;43m{m.group(0)}\x1b[0m", text)


def _highlight_terms_with_current(
    text: str, terms: list[str], current_occurrence: int | None = None
) -> str:
    """ANSI-highlight query terms, using a distinct color for the selected occurrence."""
    if not terms:
        return text
    import re

    pattern = re.compile(
        "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)), re.IGNORECASE
    )
    seen = -1

    def replace(match: re.Match[str]) -> str:
        nonlocal seen
        seen += 1
        color = (
            "30;106" if current_occurrence is not None and seen == current_occurrence else "30;43"
        )
        return f"\x1b[{color}m{match.group(0)}\x1b[0m"

    return pattern.sub(replace, text)


_BAR_STYLE = "\x1b[48;5;236;38;5;252m"


def _style_bar(text: str, *, ansi: bool) -> str:
    if not ansi:
        return text
    return f"{_BAR_STYLE}{text}\x1b[0m"


def _style_mode_label(text: str, *, active: bool, ansi: bool) -> str:
    if not ansi:
        return text
    color = "30;45" if active else "30;46"
    return f"\x1b[1;{color}m{text}\x1b[0m"


def _style_fts_prompt(text: str, *, active: bool, ansi: bool) -> str:
    if not ansi or not active:
        return text
    return f"\x1b[1;30;45m{text}\x1b[0m"


def detail_match_lines(row: EventExplorerRow, width: int, query: str) -> list[int]:
    terms = _search_terms(query)
    if not terms:
        return []
    lower_terms = [term.lower() for term in terms]
    lines = wrapped_detail_lines(row, width)
    matches: list[int] = []
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in lower_terms):
            matches.append(i)
    return matches


def detail_match_occurrences(
    row: EventExplorerRow, width: int, query: str
) -> list[tuple[int, int]]:
    terms = _search_terms(query)
    if not terms:
        return []
    import re

    pattern = re.compile(
        "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)), re.IGNORECASE
    )
    matches: list[tuple[int, int]] = []
    for line_no, line in enumerate(wrapped_detail_lines(row, width)):
        for occurrence_no, _match in enumerate(pattern.finditer(line)):
            matches.append((line_no, occurrence_no))
    return matches


def wrapped_detail_lines(row: EventExplorerRow, width: int) -> list[str]:
    """Return width-wrapped detail lines, with extracted code at the top."""
    width = max(int(width), 20)
    lines: list[str] = []
    if row.markdown is not None:
        for line in row.markdown.splitlines() or [""]:
            lines.extend(_wrap_plain_line(line, width))
        return lines
    if row.code:
        lines.append(f"code ({row.code_language}):")
        for line in row.code.rstrip("\n").splitlines() or [""]:
            lines.extend(_wrap_plain_line(line, width))
        lines.append("")
    lines.append("event:")
    for line in row.detail.splitlines():
        lines.extend(_wrap_plain_line(line, width))
    return lines


def _highlight_syntax(text: str, width: int, language: str = "python") -> str:
    try:
        import io

        from rich.console import Console as RichConsole
        from rich.syntax import Syntax

        render_width = max(int(width), 20)
        buf = io.StringIO()
        console = RichConsole(
            file=buf,
            force_terminal=True,
            color_system="256",
            width=render_width,
            _environ={"COLUMNS": str(render_width), "LINES": "25"},
        )
        console.print(
            Syntax(
                text,
                language or "python",
                theme="monokai",
                word_wrap=True,
                line_numbers=False,
                background_color="default",
            )
        )
        return buf.getvalue()
    except Exception:
        wrapped: list[str] = []
        for line in text.splitlines():
            wrapped.extend(_wrap_plain_line(line, width))
        return "\n".join(wrapped)


def _render_markdown(markdown: str, width: int) -> str:
    try:
        import io

        from rich.console import Console as RichConsole
        from rich.markdown import Markdown

        render_width = max(int(width), 20)
        buf = io.StringIO()
        console = RichConsole(
            file=buf,
            force_terminal=True,
            color_system="256",
            width=render_width,
            _environ={"COLUMNS": str(render_width), "LINES": "25"},
        )
        console.print(Markdown(markdown))
        return buf.getvalue()
    except Exception:
        wrapped: list[str] = []
        for line in markdown.splitlines():
            wrapped.extend(_wrap_plain_line(line, width))
        return "\n".join(wrapped)


def highlighted_detail_lines(
    row: EventExplorerRow,
    width: int,
    query: str = "",
    current_match_line: int | None = None,
    current_match_occurrence: int | None = None,
) -> list[str]:
    """Return detail lines with code and Python-repr event syntax highlighted."""
    width = max(int(width), 20)
    lines: list[str] = []
    terms = _search_terms(query)

    def mark(line: str) -> str:
        line_no = len(lines)
        if current_match_line is not None and line_no == current_match_line:
            return _highlight_terms_with_current(line, terms, current_match_occurrence)
        return _highlight_terms(line, terms)

    if terms:
        # Search navigation is computed from wrapped plain text. Use the same
        # representation for search rendering so the selected occurrence index
        # stays correct even when many matches share one line.
        for line in wrapped_detail_lines(row, width):
            lines.append(mark(line))
        return lines

    if row.markdown is not None:
        return _render_markdown(row.markdown, width).splitlines() or [""]
    if row.code:
        lines.append(f"code ({row.code_language}):")
        code_lines = _highlight_syntax(
            row.code.rstrip("\n"), width, row.code_language
        ).splitlines() or [""]
        lines.extend(code_lines)
        lines.append("")
    lines.append("event:")
    rendered = _highlight_syntax(row.detail, width, "python")
    lines.extend(rendered.splitlines() or [""])
    return lines


def render_event_explorer(
    model: EventExplorerModel, width: int, height: int, *, ansi: bool = False
) -> str:
    """Render the current explorer state as text/ANSI."""
    width = max(int(width), 40)
    height = max(int(height), 1)
    row = model.current
    match_count = len(model.matches)
    total = len(model.rows)
    title = "Event Explorer"
    query = f" search={model.query!r}" if model.query else ""
    pos = f" {model.cursor + 1}/{match_count}" if match_count else " 0/0"
    match_label = f" match {model.cursor + 1}/{match_count}" if model.query and match_count else ""
    header = _style_bar(
        f" {title}{pos} of {total}{match_label}{query} ".ljust(width, "─")[:width], ansi=ansi
    )
    pane_label = "events" if model.focus == "list" else "event text"
    if model.search_active:
        mode_text = "FTS MODE"
        search_prompt = f"FTS: {model.query}"
        nav_hint = "↑/↓ next match" if model.focus == "list" else "↑/↓ scroll text"
        enter_hint = "enter exit FTS"
    else:
        mode_text = "BROWSE MODE"
        search_prompt = "/ FTS"
        nav_hint = "↑/↓ matches/scroll"
        enter_hint = ""
    focus_label = f"{mode_text} · pane={pane_label}"
    footer_parts = [
        focus_label,
        "tab switch pane",
        nav_hint,
        search_prompt,
        enter_hint,
        "esc clear",
        "q close",
    ]
    footer_plain = (" " + "  ".join(part for part in footer_parts if part) + " ").ljust(width, "─")[
        :width
    ]
    if ansi:
        before_mode, after_mode = footer_plain.split(mode_text, 1)
        styled_mode = _style_mode_label(mode_text, active=model.search_active, ansi=True)
        footer = f"{_BAR_STYLE}{before_mode}{styled_mode}{_BAR_STYLE}{after_mode}\x1b[0m"
    else:
        footer = footer_plain
    body_height = max(height - 2, 0)
    if not total:
        body = ["No events recorded."]
    elif row is None:
        body = [f"No matches for {model.query!r}."]
    else:
        list_count = min(8, max(3, body_height // 3))
        half = list_count // 2
        start = max(0, model.cursor - half)
        end = min(match_count, start + list_count)
        start = max(0, end - list_count)
        body = []
        for visible_i in range(start, end):
            row_i = model.matches[visible_i]
            item = model.rows[row_i]
            marker = (
                "❯"
                if visible_i == model.cursor and model.focus == "list"
                else "•"
                if visible_i == model.cursor
                else " "
            )
            line = f"{marker} {item.tag:<8} {item.event_type:<22} {item.summary}"
            line = line[:width]
            body.append(_highlight_terms(line, _search_terms(model.query)) if ansi else line)
        divider_label = (
            f"[FTS: {model.query}] " if model.search_active or model.query else "[/: FTS] "
        )
        divider_plain = divider_label.ljust(width, "─")[:width]
        if ansi and (model.search_active or model.query):
            divider = _style_fts_prompt(divider_label, active=model.search_active, ansi=True)
            divider += "─" * max(width - len(divider_label), 0)
            body.append(divider)
        else:
            body.append(divider_plain)
        available = max(body_height - len(body), 0)
        model._last_detail_match_lines = detail_match_lines(row, width, model.query)
        model._last_detail_match_occurrences = detail_match_occurrences(row, width, model.query)
        current_match_line = None
        current_match_occurrence = None
        match_count_in_detail = len(model._last_detail_match_occurrences) or len(
            model._last_detail_match_lines
        )
        if match_count_in_detail:
            if model.search_line_cursor >= match_count_in_detail:
                model.search_line_cursor = match_count_in_detail - 1
            if model._last_detail_match_occurrences:
                current_match_line, current_match_occurrence = model._last_detail_match_occurrences[
                    model.search_line_cursor
                ]
            else:
                current_match_line = model._last_detail_match_lines[model.search_line_cursor]

        else:
            model.search_line_cursor = 0
        detail_lines = (
            highlighted_detail_lines(
                row,
                width,
                model.query,
                current_match_line=current_match_line,
                current_match_occurrence=current_match_occurrence,
            )
            if ansi
            else wrapped_detail_lines(row, width)
        )
        model._last_detail_line_count = len(detail_lines)
        detail_visible = (
            max(available - 1, 0) if model._last_detail_line_count > available else available
        )
        model._last_detail_visible_lines = detail_visible
        if (
            model.search_active
            and model.focus == "list"
            and current_match_line is not None
            and detail_visible > 0
        ):
            model.center_detail_on_line(current_match_line, detail_visible)
        else:
            model.clamp_detail_offset(detail_visible)
        if model._last_detail_line_count > available:
            scroll_label = (
                f"{'❯ ' if model.focus == 'detail' else ''}event lines {model.detail_offset + 1}-"
                f"{min(model.detail_offset + detail_visible, model._last_detail_line_count)}"
                f"/{model._last_detail_line_count}"
            )
            body.append(scroll_label[:width])
        for line in detail_lines[model.detail_offset : model.detail_offset + detail_visible]:
            body.append(line if ansi else line[:width])
    body = body[:body_height]
    body.extend("" for _ in range(body_height - len(body)))
    return "\n".join([header, *body, footer])
