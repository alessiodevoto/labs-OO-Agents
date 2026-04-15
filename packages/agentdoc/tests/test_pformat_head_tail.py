# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for element-aware head+tail truncation in pformat.

When max_length fires for ordered containers (list, tuple, dict), pformat should
show the first n_head elements AND the last n_tail elements, with a prose notice
in the middle — matching numpy/pandas style.

Unordered containers (set, frozenset) keep head-only (no stable order).
Budget-based truncation (_budget / max_total_chars) keeps head-only (can't
collect tail without materialising the full sequence).
"""

import re

from agentdoc import pformat, truncating_pformat


class TestListHeadTail:
    """Lists with max_length show head AND tail elements."""

    def test_large_list_shows_tail(self):
        """Items at the end of a large list are visible after truncation."""
        items = list(range(100))
        result = pformat(items, max_length=10)
        # Head: items 0-4 must appear
        assert "0" in result
        assert "4" in result
        # Tail: items 95-99 must appear (was lost before this fix)
        assert "99" in result
        assert "95" in result

    def test_large_list_shows_not_shown_notice(self):
        """The truncation notice tells how many items were dropped."""
        items = list(range(100))
        result = pformat(items, max_length=10)
        assert "not shown" in result

    def test_large_list_head_and_tail_split(self):
        """Head takes ceiling(max_length/2), tail takes floor(max_length/2)."""
        items = list(range(200))
        result = pformat(items, max_length=10)
        # Head: 5 items (0..4), tail: 5 items (195..199)
        assert "0" in result
        assert "4" in result
        assert "195" in result
        assert "199" in result
        # Item 5 should NOT appear (it's in the dropped middle)
        # Item 194 should NOT appear (it's in the dropped middle)
        assert not re.search(r"\b194\b", result)  # 194 as standalone number, not part of 1940+

    def test_list_under_max_length_unchanged(self):
        """Lists within max_length show all items, no notice."""
        items = [1, 2, 3]
        result = pformat(items, max_length=10)
        assert "1" in result
        assert "2" in result
        assert "3" in result
        assert "not shown" not in result

    def test_list_exactly_max_length_unchanged(self):
        """List with exactly max_length items shows all, no truncation."""
        items = list(range(10))
        result = pformat(items, max_length=10)
        assert "not shown" not in result
        for i in range(10):
            assert str(i) in result

    def test_list_max_length_one_shows_no_tail(self):
        """max_length=1: only 1 item shown, no tail (can't split 1 into head+tail)."""
        items = list(range(100))
        result = pformat(items, max_length=1)
        # Should have at most 1 head item (item 0)
        assert "0" in result
        # Should still have a truncation notice
        assert "not shown" in result or "+99" in result or "more" in result

    def test_list_max_length_two_shows_one_head_one_tail(self):
        """max_length=2: first item (head) and last item (tail) shown."""
        items = list(range(100))
        result = pformat(items, max_length=2)
        assert "0" in result  # head
        assert "99" in result  # tail

    def test_list_brackets_are_balanced(self):
        """Output is syntactically valid — opening and closing brackets match."""
        items = list(range(100))
        result = pformat(items, max_length=10)
        assert result.startswith("[")
        assert result.endswith("]")

    def test_list_items_dropped_count_in_notice(self):
        """Notice reports the correct number of dropped items."""
        items = list(range(100))  # 100 items, max_length=10 → 5 head + 5 tail → 90 dropped
        result = pformat(items, max_length=10)
        assert "90" in result  # 90 items not shown

    def test_small_list_with_max_length_not_truncated(self):
        """List shorter than max_length: all items shown."""
        result = pformat([10, 20, 30], max_length=50)
        assert "10" in result
        assert "20" in result
        assert "30" in result
        assert "not shown" not in result


class TestTupleHeadTail:
    """Tuples show head+tail like lists (ordered)."""

    def test_large_tuple_shows_tail(self):
        items = tuple(range(100))
        result = pformat(items, max_length=10)
        assert "99" in result
        assert "not shown" in result

    def test_tuple_brackets_balanced(self):
        items = tuple(range(100))
        result = pformat(items, max_length=10)
        assert result.startswith("(")
        assert result.endswith(")")


class TestDictHeadTail:
    """Dicts show head+tail keys when max_length fires (dicts are insertion-ordered)."""

    def test_large_dict_shows_last_keys(self):
        """Last keys of a large dict appear after truncation."""
        d = {str(i): i for i in range(100)}
        result = pformat(d, max_length=10)
        # Head: keys '0'..'4'
        assert "'0'" in result
        # Tail: keys '95'..'99'
        assert "'99'" in result

    def test_large_dict_notice(self):
        d = {str(i): i for i in range(100)}
        result = pformat(d, max_length=10)
        assert "not shown" in result

    def test_dict_brackets_balanced(self):
        d = {str(i): i for i in range(100)}
        result = pformat(d, max_length=10)
        assert result.startswith("{")
        assert result.endswith("}")

    def test_small_dict_unchanged(self):
        d = {"a": 1, "b": 2}
        result = pformat(d, max_length=10)
        assert "'a'" in result
        assert "'b'" in result
        assert "not shown" not in result


class TestUnorderedCollections:
    """Sets and frozensets: head-only (no stable order → tail not meaningful)."""

    def test_set_does_not_crash(self):
        """Large sets still truncate without raising."""
        s = set(range(100))
        result = pformat(s, max_length=10)
        assert result  # non-empty
        assert "{" in result or "frozenset" in result.lower() or "set" in result.lower()

    def test_frozenset_does_not_crash(self):
        s = frozenset(range(100))
        result = pformat(s, max_length=10)
        assert result


class TestMaxCharsTruncation:
    """max_chars cap belongs to truncating_pformat, not pformat."""

    def test_max_chars_bounds_output(self):
        """Large list with max_chars: output bounded, prose notice included."""
        items = list(range(1_000_000))
        result = truncating_pformat(items, max_chars=1000)
        assert len(result) < 10_000  # bounded


class TestExpandedFormat:
    """Expanded (multiline) format paths for head+tail."""

    def test_expanded_list_head_tail(self):
        """Expanded list: head+tail notice present, brackets balanced, no comma before bracket."""
        items = list(range(20))
        result = pformat(items, max_length=6, expand_all=True)
        assert result.startswith("[")
        assert result.endswith("]")
        assert "not shown" in result
        assert "0" in result
        assert "19" in result
        assert ",]" not in result

    def test_expanded_dict_head_tail(self):
        """Expanded dict: head+tail notice present, brackets balanced."""
        d = {str(i): i for i in range(20)}
        result = pformat(d, max_length=6, expand_all=True)
        assert result.startswith("{")
        assert result.endswith("}")
        assert "not shown" in result
        assert "'0'" in result
        assert "'19'" in result


class TestNestedContainers:
    """Nested containers: inner containers use their own max_length independently."""

    def test_outer_head_tail_inner_normal(self):
        """Outer list gets head+tail; inner lists are formatted normally."""
        outer = [[i, i + 1] for i in range(50)]
        result = pformat(outer, max_length=10)
        # Outer: head+tail
        assert "not shown" in result
        assert "[0," in result or "[0" in result  # first item
        # Last item [48, 49] should appear in tail
        assert "49" in result
