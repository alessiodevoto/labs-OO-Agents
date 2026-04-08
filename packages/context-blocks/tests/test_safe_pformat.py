"""Tests for safe_pformat — bounded pre-serialization helper."""

import pytest

from context_blocks.utils import _MAX_PRE_FORMAT_CHARS, safe_pformat


class TestSafePformat:
    """safe_pformat must never OOM and must include a truncation notice when capped."""

    def test_small_list_unchanged(self):
        """Small containers pass through without truncation notice."""
        result = safe_pformat([1, 2, 3])
        assert "1" in result
        assert "2" in result
        assert "3" in result
        assert "truncation" not in result.lower()

    def test_small_string_unchanged(self):
        """Small strings pass through without truncation notice."""
        result = safe_pformat("hello world")
        assert "hello world" in result
        assert "truncation" not in result.lower()

    def test_large_list_is_bounded(self):
        """A list of 1_000_000 ints does not produce a gigantic string."""
        huge = list(range(1_000_000))
        result = safe_pformat(huge)
        # Must be bounded well below the full repr (~7 MB).
        # Total = max_chars (500 K) + truncation notice overhead (~80 chars).
        assert len(result) < 501_000

    def test_large_string_is_capped(self):
        """A 2 MB string is capped at _MAX_PRE_FORMAT_CHARS."""
        huge = "x" * 2_000_000
        result = safe_pformat(huge)
        # Default cap is _MAX_PRE_FORMAT_CHARS (500 K); result must be well under 2 MB
        assert len(result) <= _MAX_PRE_FORMAT_CHARS + 500  # cap + notice overhead
        assert "Output too large" in result

    def test_capped_output_has_truncation_notice(self):
        """When the cap fires, a prose truncation notice is prepended."""
        # A plain string longer than the cap triggers the fast path directly.
        huge = "a" * (_MAX_PRE_FORMAT_CHARS + 100_000)
        result = safe_pformat(huge)
        assert result.startswith("Output too large")
        assert "chars not shown" in result

    def test_truncation_notice_has_head_and_tail(self):
        """String truncation notice shows both head and tail portions."""
        huge = "b" * (_MAX_PRE_FORMAT_CHARS + 1)
        result = safe_pformat(huge)
        assert "Output too large" in result
        assert "Showing first" in result
        assert "and last" in result
        assert "chars not shown" in result

    def test_container_with_many_long_strings_is_capped(self):
        """A dict of many long string values can exceed the cap via pformat."""
        # 60 keys × 10 K string each → pformat produces ~600 K chars, cap at 500 K.
        big = {str(i): "x" * 12_000 for i in range(60)}
        result = safe_pformat(big)
        assert len(result) <= _MAX_PRE_FORMAT_CHARS + 500  # cap + notice overhead

    def test_none_value(self):
        """None formats safely."""
        result = safe_pformat(None)
        assert result == "None"

    def test_dict_is_bounded(self):
        """A deeply nested dict is rendered without exploding."""
        big_dict = {str(i): list(range(200)) for i in range(1000)}
        result = safe_pformat(big_dict)
        assert len(result) < 2_000_000  # must not materialise the full ~800 KB repr

    def test_string_head_tail_50_50_split(self):
        """Large string uses 50/50 head+tail split."""
        # 1001 chars with max_chars=1000 → head=500, tail=500, dropped=1
        s = "A" * 500 + "x" + "Z" * 500
        result = safe_pformat(s, max_chars=1000)
        assert "A" * 10 in result  # head present
        assert "Z" * 10 in result  # tail present
        assert "500" in result  # sizes in notice
        assert "chars not shown" in result

    def test_string_preserves_start_and_end(self):
        """Head+tail format preserves both ends of a large string."""
        s = "START" + "x" * 10_000 + "END"
        result = safe_pformat(s, max_chars=100)
        assert "START" in result
        assert "END" in result

    def test_string_reports_dropped_chars(self):
        """Notice reports the correct number of dropped chars."""
        s = "x" * 2000
        result = safe_pformat(s, max_chars=1000)
        # head=500, tail=500, dropped=1000
        assert "1,000 chars not shown" in result

    def test_nonstring_abort_early_bounded(self):
        """Non-string with max_chars triggers abort-early, output bounded."""
        big = list(range(1_000_000))
        result = safe_pformat(big, max_chars=5000)
        assert len(result) < 10_000  # well under full repr (~7MB)
        assert "Output too large" in result

    def test_nonstring_abort_early_notice_format(self):
        """Abort-early notice reports actual and shown sizes (head+tail)."""
        big = list(range(1_000_000))
        result = safe_pformat(big, max_chars=5000)
        assert "Output too large" in result
        assert "Showing first 2,500 and last 2,500 chars" in result

    def test_max_chars_one_does_not_crash(self):
        """max_chars=1 is valid and returns a truncated result."""
        result = safe_pformat("hello", max_chars=1)
        assert "Output too large" in result

    def test_rejects_max_chars_zero(self):
        with pytest.raises(ValueError, match="max_chars must be > 0"):
            safe_pformat("hello", max_chars=0)

    def test_rejects_max_chars_negative(self):
        with pytest.raises(ValueError, match="max_chars must be > 0"):
            safe_pformat([1, 2, 3], max_chars=-1)
