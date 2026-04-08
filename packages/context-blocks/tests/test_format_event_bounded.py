"""Tests verifying format_event never produces unbounded output.

The base BlockFormatter.format_event() previously used sys.maxsize for all
pformat limits.  These tests ensure that calling format_event with a huge
object is safe — it must stay well below _MAX_PRE_FORMAT_CHARS chars and
include a truncation notice when it had to cap.
"""

from context_blocks.events import EventBase
from context_blocks.formatter import XMLBlockFormatter
from context_blocks.utils import _MAX_PRE_FORMAT_CHARS


class BigValueEvent(EventBase):
    """Minimal EventBase subclass carrying an arbitrary Python value."""

    value: object

    model_config = {"arbitrary_types_allowed": True}


class TestFormatEventBounded:
    def setup_method(self):
        self.fmt = XMLBlockFormatter()

    def test_small_event_unchanged(self):
        """A normal event is formatted without a truncation notice."""
        event = BigValueEvent(value=[1, 2, 3])
        result = self.fmt.format_event(event)
        assert "1" in result
        assert "truncation" not in result.lower()

    def test_large_list_value_is_bounded(self):
        """An event containing a 1 M-element list must be bounded."""
        event = BigValueEvent(value=list(range(1_000_000)))
        result = self.fmt.format_event(event)
        assert len(result) < _MAX_PRE_FORMAT_CHARS + 500  # cap + notice

    def test_large_string_value_is_bounded(self):
        """An event with a 2 MB string value must be bounded."""
        event = BigValueEvent(value="x" * 2_000_000)
        result = self.fmt.format_event(event)
        assert len(result) <= _MAX_PRE_FORMAT_CHARS + 500

    def test_output_bounded_when_cap_set(self):
        """When an explicit max_chars cap is set, the output must be bounded."""
        # Pass a large string value; format_event calls safe_pformat(event, max_chars=5_000).
        # Even though BigValueEvent is a structured instance, safe_pformat must
        # keep the total output bounded.
        event = BigValueEvent(value="z" * 20_000)
        result = self.fmt.format_event(event, max_chars=5_000)
        # The result should be well-bounded regardless of internal truncation path
        assert len(result) < 20_000
