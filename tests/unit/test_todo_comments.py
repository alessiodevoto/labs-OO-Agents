# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``TodoManager.comment`` / ``comments`` — progress journalling.

Comments are the canonical way an SWE skill (brainstorm / root-cause / tdd /
review / ship) logs what happened at each step so the next turn — or a
different skill — can read the history without re-deriving it.
"""

import pytest

from nemo_oo_agents.tools.todo import TodoComment, TodoManager


def test_comment_returns_stored_instance():
    tm = TodoManager()
    t = tm.add("Fix bug")
    c = tm.comment(t.id, "root cause: races on refresh")
    assert isinstance(c, TodoComment)
    assert c.body == "root cause: races on refresh"
    assert c.created_at  # non-empty timestamp


def test_comment_on_missing_todo_returns_none():
    tm = TodoManager()
    assert tm.comment("does-not-exist", "hello") is None


def test_comments_returns_empty_list_for_new_todo():
    tm = TodoManager()
    t = tm.add("New")
    assert tm.comments(t.id) == []


def test_comments_are_chronological_append_only():
    tm = TodoManager()
    t = tm.add("Multi-step work")
    tm.comment(t.id, "first")
    tm.comment(t.id, "second")
    tm.comment(t.id, "third")
    bodies = [c.body for c in tm.comments(t.id)]
    assert bodies == ["first", "second", "third"]


def test_comments_returns_empty_for_missing_todo():
    tm = TodoManager()
    assert tm.comments("nope") == []


def test_comments_survive_snapshot_round_trip():
    """Snapshot → restore must preserve the comment log verbatim."""
    tm = TodoManager()
    t = tm.add("With history")
    tm.comment(t.id, "one")
    tm.comment(t.id, "two")

    state = tm.to_dict()
    restored = TodoManager()
    restored.from_dict(state)

    preserved = restored.comments(t.id)
    assert [c.body for c in preserved] == ["one", "two"]


def test_comment_does_not_overwrite_notes():
    """Comments and ``notes`` are independent fields — append to one doesn't
    touch the other."""
    tm = TodoManager()
    t = tm.add("Item", notes="static note")
    tm.comment(t.id, "progress log entry")
    assert t.notes == "static note"
    assert [c.body for c in tm.comments(t.id)] == ["progress log entry"]


def test_multiple_todos_keep_comments_separate():
    tm = TodoManager()
    a = tm.add("A")
    b = tm.add("B")
    tm.comment(a.id, "about A")
    tm.comment(b.id, "about B")
    assert [c.body for c in tm.comments(a.id)] == ["about A"]
    assert [c.body for c in tm.comments(b.id)] == ["about B"]


@pytest.mark.parametrize("body", ["", "just some text", "multi\nline\nbody", "🔍 emoji"])
def test_comment_accepts_arbitrary_body(body):
    tm = TodoManager()
    t = tm.add("T")
    c = tm.comment(t.id, body)
    assert c is not None
    assert c.body == body
