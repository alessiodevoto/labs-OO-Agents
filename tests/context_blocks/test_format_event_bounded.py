# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests verifying format_event renders events safely.

Block-level head/tail string truncation has been removed. Strings pass through
verbatim; non-strings get pformat with optional ``max_chars`` as an
OOM-safety net (TruncatingStringIO). These tests ensure ``format_event`` stays
well-bounded for non-string values when ``max_chars`` is set.
"""

from nemo_oo_agents.context_blocks.events import EventBase
from nemo_oo_agents.context_blocks.formatter import XMLBlockFormatter


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

    def test_large_list_value_bounded_with_max_chars(self):
        """A non-string value with an explicit ``max_chars`` cap is OOM-bounded."""
        event = BigValueEvent(value=list(range(1_000_000)))
        result = self.fmt.format_event(event, max_chars=10_000)
        # TruncatingStringIO keeps output well under the full ~7MB repr
        assert len(result) < 20_000

    def test_large_string_value_uses_marker_family(self):
        """A string field NESTED inside a structured event gets the marker
        family treatment when ``event_format`` provides a ``max_string`` bound.
        Without ``event_format``, hardcoded fallbacks are gone — the caller is
        expected to pass an explicit FormatConfig."""
        from nemo_oo_agents.config.truncation_config import FormatConfig

        event = BigValueEvent(value="x" * 2_000_000)
        result = self.fmt.format_event(
            event,
            max_chars=10_000,
            event_format=FormatConfig(max_string=500, max_length=50, max_depth=4),
        )
        # Marker family kicks in for the nested string field
        assert "str(len=2000000" in result
        # And the result is bounded well below the original 2 MB
        assert len(result) < 2000

    def test_default_no_cap(self):
        """Default ``format_event`` call has no max_chars; non-string content
        renders fully (per-value bounds come from spec() / cfg.events.* upstream)."""
        event = BigValueEvent(value=[1, 2, 3, 4, 5])
        result = self.fmt.format_event(event)
        for n in (1, 2, 3, 4, 5):
            assert str(n) in result
