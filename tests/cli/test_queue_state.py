# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``QueueState`` — pure state container for the type-ahead queue.

The state is rendered into the dynamic prompt prefix by ``render_prompt``.
Submissions come from the Enter keybinding; pops come from the Up-arrow
keybinding when the input buffer is empty.
"""

from nemo_oo_agents_cli.tui.queue_state import QueueState, render_prompt

# ---------------------------------------------------------------------------
# submit()
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_plain_text_creates_message(self):
        q = QueueState()
        q.submit("hello")
        assert q.messages == ["hello"]
        assert q.commands == []

    def test_second_plain_text_appends_to_same_message(self):
        q = QueueState()
        q.submit("line one")
        q.submit("line two")
        assert q.messages == ["line one\nline two"]
        assert q.commands == []

    def test_three_plain_submits_all_in_one_message(self):
        q = QueueState()
        q.submit("a")
        q.submit("b")
        q.submit("c")
        assert q.messages == ["a\nb\nc"]

    def test_slash_command_goes_to_commands(self):
        q = QueueState()
        q.submit("/help")
        assert q.messages == []
        assert q.commands == ["/help"]

    def test_bang_command_goes_to_commands(self):
        q = QueueState()
        q.submit("!ls")
        assert q.commands == ["!ls"]

    def test_empty_text_ignored(self):
        q = QueueState()
        q.submit("")
        assert q.is_empty

    def test_whitespace_only_ignored(self):
        q = QueueState()
        q.submit("   ")
        assert q.is_empty

    def test_message_does_not_merge_into_command(self):
        q = QueueState()
        q.submit("/help")
        q.submit("follow-up text")
        assert q.commands == ["/help"]
        assert q.messages == ["follow-up text"]

    def test_command_after_message_keeps_separate(self):
        q = QueueState()
        q.submit("work on X")
        q.submit("/help")
        assert q.messages == ["work on X"]
        assert q.commands == ["/help"]

    def test_second_command_appended_as_separate_item(self):
        q = QueueState()
        q.submit("/help")
        q.submit("/exit")
        assert q.commands == ["/help", "/exit"]

    def test_multiline_input_stays_as_one_submission(self):
        q = QueueState()
        q.submit("line1\nline2")
        assert q.messages == ["line1\nline2"]

    def test_multiline_then_single_combines(self):
        q = QueueState()
        q.submit("block1\nblock2")
        q.submit("tail")
        assert q.messages == ["block1\nblock2\ntail"]


# ---------------------------------------------------------------------------
# pop_last_for_edit()
# ---------------------------------------------------------------------------


class TestPopLastForEdit:
    def test_pop_command_preferred_over_message(self):
        q = QueueState(messages=["msg"], commands=["/cmd"])
        assert q.pop_last_for_edit() == "/cmd"
        assert q.commands == []
        assert q.messages == ["msg"]

    def test_pop_message_when_no_commands(self):
        q = QueueState(messages=["msg"])
        assert q.pop_last_for_edit() == "msg"
        assert q.messages == []

    def test_pop_multi_line_message_returns_full_block(self):
        q = QueueState(messages=["line1\nline2\nline3"])
        assert q.pop_last_for_edit() == "line1\nline2\nline3"
        assert q.messages == []

    def test_pop_empty_returns_none(self):
        q = QueueState()
        assert q.pop_last_for_edit() is None

    def test_pop_most_recent_command(self):
        q = QueueState(commands=["/first", "/second"])
        assert q.pop_last_for_edit() == "/second"
        assert q.commands == ["/first"]


# ---------------------------------------------------------------------------
# joining / draining
# ---------------------------------------------------------------------------


class TestJoining:
    def test_joined_messages_single_item(self):
        q = QueueState(messages=["hello world"])
        assert q.as_joined_messages() == "hello world"

    def test_joined_messages_preserves_multiline(self):
        q = QueueState(messages=["line1\nline2"])
        assert q.as_joined_messages() == "line1\nline2"

    def test_pending_text_messages_only(self):
        q = QueueState(messages=["msg"])
        assert q.as_pending_text() == "msg"

    def test_pending_text_commands_only(self):
        q = QueueState(commands=["/help"])
        assert q.as_pending_text() == "/help"

    def test_pending_text_combines_both_with_blank_line(self):
        q = QueueState(messages=["msg"], commands=["/cmd"])
        assert q.as_pending_text() == "msg\n\n/cmd"

    def test_pending_text_empty_is_empty_string(self):
        assert QueueState().as_pending_text() == ""


# ---------------------------------------------------------------------------
# flags
# ---------------------------------------------------------------------------


class TestFlags:
    def test_empty_on_init(self):
        assert QueueState().is_empty

    def test_not_empty_with_message(self):
        assert not QueueState(messages=["hi"]).is_empty

    def test_not_empty_with_command(self):
        assert not QueueState(commands=["/help"]).is_empty

    def test_clear_wipes_everything(self):
        q = QueueState(messages=["m"], commands=["/c"])
        q.clear()
        assert q.is_empty
        assert q.messages == []
        assert q.commands == []


# ---------------------------------------------------------------------------
# render_prompt — QueueState → FormattedText fragments
# ---------------------------------------------------------------------------


def _joined(fragments) -> str:
    """Concat the text portions of a FormattedText fragment list."""
    return "".join(frag[1] for frag in fragments)


class TestRenderPrompt:
    def test_empty_state_is_just_prompt(self):
        fragments = render_prompt(QueueState())
        assert _joined(fragments) == "❯ "

    def test_thinking_prepends_spinner_line(self):
        state = QueueState(thinking=True, spinner_frame="⠋", thinking_message="thinking...")
        text = _joined(render_prompt(state))
        assert text.startswith("⠋ thinking...\n")
        assert text.endswith("❯ ")

    def test_thinking_false_no_spinner(self):
        state = QueueState(thinking=False)
        text = _joined(render_prompt(state))
        assert "thinking" not in text

    def test_single_queued_message_has_bar_prefix(self):
        state = QueueState(messages=["hello"])
        text = _joined(render_prompt(state))
        assert "│ hello\n" in text
        assert text.endswith("❯ ")

    def test_multi_line_message_renders_each_line(self):
        state = QueueState(messages=["line1\nline2"])
        text = _joined(render_prompt(state))
        assert "│ line1\n" in text
        assert "│ line2\n" in text

    def test_queued_command_has_bar_prefix(self):
        state = QueueState(commands=["/help"])
        text = _joined(render_prompt(state))
        assert "│ /help\n" in text

    def test_ordering_spinner_messages_commands_prompt(self):
        state = QueueState(
            thinking=True,
            spinner_frame="⠋",
            messages=["msg"],
            commands=["/cmd"],
        )
        text = _joined(render_prompt(state))
        spinner_idx = text.index("thinking")
        msg_idx = text.index("msg")
        cmd_idx = text.index("/cmd")
        prompt_idx = text.index("❯")
        assert spinner_idx < msg_idx < cmd_idx < prompt_idx

    def test_multiple_messages_all_rendered(self):
        # Normally submit() merges into one, but the state supports multiple.
        state = QueueState(messages=["m1", "m2"])
        text = _joined(render_prompt(state))
        assert "│ m1\n" in text
        assert "│ m2\n" in text

    def test_prompt_char_customizable(self):
        fragments = render_prompt(QueueState(), prompt_char="> ")
        assert _joined(fragments).endswith("> ")

    def test_spinner_frame_reflected_in_output(self):
        state = QueueState(thinking=True, spinner_frame="⠹")
        text = _joined(render_prompt(state))
        assert "⠹ " in text
