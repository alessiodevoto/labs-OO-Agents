"""Tests for pformat abort-early budget (_budget / max_total_chars).

Covers:
- Flat containers (no nesting): budget tracks exactly
- Nested containers: budget must NOT double-count across levels
- Deep nesting (3+ levels): correct accounting through all levels
- Budget exhausted mid-nesting: inner truncation, outer stops correctly
- Budget exactly matches output: no off-by-one over-stopping
- Mixed dict+list nesting
- Expanded format (expand_all=True): same accounting rules
- Edge cases: empty containers, single-element, budget=1
"""

from agentdoc import pformat


class TestFlatContainers:
    """Flat containers with primitive values — no nesting, no double-counting risk."""

    def test_large_list_bounded_by_max_total_chars(self):
        big = list(range(1_000_000))
        truncated_out = [False]
        result = pformat(big, max_total_chars=1000, _truncated_out=truncated_out)
        assert truncated_out[0] is True
        assert len(result) < 5000  # well under full repr

    def test_small_object_not_truncated(self):
        truncated_out = [False]
        result = pformat([1, 2, 3], max_total_chars=10000, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "1" in result and "2" in result and "3" in result

    def test_no_max_total_chars_formats_fully(self):
        obj = list(range(100))
        result = pformat(obj)
        assert "99" in result  # all elements present

    def test_truncated_out_none_does_not_crash(self):
        result = pformat([1, 2, 3], max_total_chars=1000, _truncated_out=None)
        assert "1" in result

    def test_flat_list_budget_tracks_output_length(self):
        """Budget consumed should equal actual output length for a flat list."""
        obj = [1, 2, 3, 4, 5]
        full = pformat(obj)
        budget = [len(full) + 1]  # budget exactly 1 more than needed
        result = pformat(obj, max_total_chars=budget[0])
        # Should NOT truncate — budget is sufficient
        assert "1" in result and "5" in result
        assert "more" not in result

    def test_flat_dict_budget_tracks_output_length(self):
        """Budget consumed should equal actual output length for a flat dict."""
        obj = {"a": 1, "b": 2, "c": 3}
        full = pformat(obj)
        # Budget just enough: no truncation
        result_full = pformat(obj, max_total_chars=len(full) + 10)
        assert "more" not in result_full
        # Budget too tight: truncation
        truncated_out = [False]
        pformat(obj, max_total_chars=5, _truncated_out=truncated_out)
        assert truncated_out[0] is True


class TestNestedContainers:
    """Nested containers: the critical double-counting regression suite."""

    def test_nested_list_no_double_counting(self):
        """Budget for [[1,2,3],[4,5,6]] should equal actual output length."""
        obj = [[1, 2, 3], [4, 5, 6]]
        full = pformat(obj)
        # A budget just barely sufficient should NOT over-truncate
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 5, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "1" in result and "6" in result

    def test_nested_dict_no_double_counting(self):
        """Budget for {a: {x:1,y:2}, b: {x:3,y:4}} should equal actual output length."""
        obj = {"a": {"x": 1, "y": 2}, "b": {"x": 3, "y": 4}}
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 5, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "x" in result and "4" in result

    def test_dict_with_list_values_no_double_counting(self):
        """Budget for {k: [1,2,3,4,5]} should equal actual output length."""
        obj = {"items": [1, 2, 3, 4, 5], "more": [6, 7, 8]}
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 5, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "8" in result

    def test_budget_respected_in_nested_dict(self):
        """Large nested dict stops within a reasonable bound of the budget."""
        big = {str(i): list(range(100)) for i in range(1000)}
        truncated_out = [False]
        result = pformat(big, max_total_chars=500, _truncated_out=truncated_out)
        assert truncated_out[0] is True
        # Result should be close to the budget, not 10x under due to double-counting.
        # With correct accounting, result length should be within 2x of budget.
        assert len(result) < 2000  # much tighter than old 3000 bound
        assert len(result) > 50  # and not empty

    def test_list_of_lists_budget_proportional_to_output(self):
        """[[0..9], [0..9], ...] 10 inner lists: result size ~ budget."""
        obj = [list(range(10)) for _ in range(10)]
        full = pformat(obj)
        # Budget at half the full length should show roughly half the outer items
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) // 2, _truncated_out=truncated_out)
        assert truncated_out[0] is True
        # Should not be absurdly short relative to budget (no severe double-counting)
        assert len(result) >= len(full) // 4  # at least 1/4 of full output

    def test_nested_at_exact_budget(self):
        """Budget exactly equal to full output length: no truncation."""
        obj = {"k": [1, 2, 3]}
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full), _truncated_out=truncated_out)
        # Exact budget: should not truncate (budget == output, not budget < output)
        # The budget check is _budget[0] <= 0; at exactly 0 it stops AFTER this item.
        # So with budget == len(full), the last item exhausts the budget but is included.
        assert "1" in result and "3" in result


class TestDeepNesting:
    """3+ levels of nesting — budget must chain correctly through all levels."""

    def test_three_level_list_no_double_counting(self):
        """[[[1,2],[3,4]],[[5,6],[7,8]]] — 3 levels deep, budget = full length."""
        obj = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 10, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "8" in result

    def test_three_level_dict_no_double_counting(self):
        obj = {"a": {"b": {"c": 1, "d": 2}}, "e": {"f": {"g": 3}}}
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 10, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "3" in result

    def test_deep_nesting_budget_is_not_exhausted_prematurely(self):
        """With correct accounting, a budget >> full_size never truncates."""
        obj = [[[i for i in range(5)] for _ in range(3)] for _ in range(3)]
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) * 10, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert result == full  # identical output


class TestBudgetExhaustedMidNesting:
    """Budget exhausted inside a nested container — inner and outer both stop."""

    def test_inner_list_truncates_outer_stops_after(self):
        """If inner list truncation exhausts budget, outer stops after that item."""
        # Outer list: 3 items, each item is [0..99]
        obj = [list(range(100)), list(range(100)), list(range(100))]
        # Budget enough for ~1 inner list
        inner_one = pformat(list(range(100)))
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(inner_one) + 5, _truncated_out=truncated_out)
        assert truncated_out[0] is True
        # Should show the truncation indicator for remaining outer items
        assert "more" in result

    def test_inner_dict_truncates_outer_continues_correctly(self):
        """Inner dict truncates; outer dict budget reflects actual output, not double."""
        big_inner = {str(i): i for i in range(200)}
        obj = {"first": big_inner, "second": {"a": 1}}
        truncated_out = [False]
        result = pformat(obj, max_total_chars=100, _truncated_out=truncated_out)
        assert truncated_out[0] is True
        assert "first" in result  # at minimum the first key


class TestExpandedFormat:
    """expand_all=True uses the expanded formatting path — same accounting."""

    def test_expanded_flat_list_not_double_counted(self):
        obj = [1, 2, 3, 4, 5]
        full = pformat(obj, expand_all=True)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 10, expand_all=True, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "5" in result

    def test_expanded_nested_list_no_double_counting(self):
        obj = [[1, 2, 3], [4, 5, 6]]
        full = pformat(obj, expand_all=True)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 10, expand_all=True, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "6" in result

    def test_expanded_dict_with_list_values(self):
        obj = {"a": [1, 2, 3], "b": [4, 5, 6]}
        full = pformat(obj, expand_all=True)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 10, expand_all=True, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "6" in result


class TestEdgeCases:
    """Edge cases: empty containers, single-element, tiny budgets."""

    def test_empty_list_with_budget(self):
        result = pformat([], max_total_chars=10)
        assert result == "[]"

    def test_empty_dict_with_budget(self):
        result = pformat({}, max_total_chars=10)
        assert result == "{}"

    def test_single_element_list(self):
        truncated_out = [False]
        result = pformat([42], max_total_chars=10, _truncated_out=truncated_out)
        assert "42" in result
        assert truncated_out[0] is False

    def test_budget_of_one_still_shows_something(self):
        """Budget=1 is absurdly tight; must not crash and must stop quickly."""
        result = pformat(list(range(10000)), max_total_chars=1)
        assert isinstance(result, str)
        assert len(result) < 500  # stopped quickly

    def test_nested_empty_containers(self):
        obj = {"a": [], "b": {}, "c": [[], []]}
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 5, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert result == full

    def test_tuple_and_set_nesting(self):
        """Tuples and sets use _format_sequence too — same budget fix applies."""
        obj = [(1, 2), (3, 4)]
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 5, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "4" in result

    def test_mixed_types_in_list(self):
        obj = [1, "hello", {"a": 1}, [2, 3]]
        full = pformat(obj)
        truncated_out = [False]
        result = pformat(obj, max_total_chars=len(full) + 10, _truncated_out=truncated_out)
        assert truncated_out[0] is False
        assert "hello" in result

    def test_large_flat_value_in_nested_container(self):
        """A single huge string inside a list exhausts budget; outer stops after."""
        big_str = "x" * 500
        obj = [big_str, "small1", "small2"]
        truncated_out = [False]
        result = pformat(obj, max_total_chars=200, _truncated_out=truncated_out)
        assert truncated_out[0] is True
        # The big string (first item) should be present, subsequent items truncated
        assert "more" in result
