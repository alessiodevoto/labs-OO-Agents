# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TDD: gl-89 — char approximation fallback when LLM lacks count_tokens.

Change 6 of truncation-2.0: When max_context_tokens/max_event_tokens is set
but LLM has no count_tokens method, use len(text) // 4 and log a warning
instead of raising ValueError.
"""

import logging
from unittest.mock import Mock, patch

import pytest


class TestTokenCounterFallback:
    """_get_token_counter must provide approximation when LLM lacks count_tokens."""

    def test_get_token_counter_is_importable(self):
        # TDD: will fail until Change 6 is implemented
        from nemo_oo_agents.runtime.actor import _get_token_counter
        assert callable(_get_token_counter)

    def test_uses_llm_count_tokens_when_available(self):
        # TDD: will fail until Change 6 is implemented
        from nemo_oo_agents.runtime.actor import _get_token_counter

        llm = Mock()
        llm.count_tokens = Mock(return_value=7)
        counter = _get_token_counter(llm)
        assert counter("any text") == 7
        llm.count_tokens.assert_called_once_with("any text")

    def test_fallback_when_no_count_tokens(self):
        # TDD: will fail until Change 6 is implemented
        from nemo_oo_agents.runtime.actor import _get_token_counter

        llm = Mock(spec=[])  # no count_tokens attribute
        counter = _get_token_counter(llm)
        assert counter("x" * 100) == 25   # 100 // 4

    def test_fallback_formula_is_len_div_4(self):
        # TDD: will fail until Change 6 is implemented
        from nemo_oo_agents.runtime.actor import _get_token_counter

        llm = Mock(spec=[])
        counter = _get_token_counter(llm)
        assert counter("") == 0
        assert counter("xxxx") == 1
        assert counter("x" * 1000) == 250

    def test_fallback_logs_warning(self):
        # TDD: will fail until Change 6 is implemented
        from nemo_oo_agents.runtime.actor import _get_token_counter

        llm = Mock(spec=[])
        with patch("nemo_oo_agents.runtime.actor.logger") as mock_logger:
            _get_token_counter(llm)
            assert mock_logger.warning.called
            warning_text = str(mock_logger.warning.call_args).lower()
            assert "approxim" in warning_text or "token" in warning_text

    def test_no_value_error_raised_for_missing_count_tokens(self):
        # TDD: will fail until Change 6 is implemented
        # Old behavior: render_context raised ValueError if count_tokens=None.
        # New behavior: _get_token_counter returns a fallback.
        from nemo_oo_agents.runtime.actor import _get_token_counter

        llm = Mock(spec=[])
        try:
            counter = _get_token_counter(llm)
            result = counter("hello world")
            assert isinstance(result, int)
        except ValueError:
            pytest.fail("Should not raise ValueError — fallback should be used")

    def test_counter_returns_non_negative_int(self):
        # TDD: will fail until Change 6 is implemented
        from nemo_oo_agents.runtime.actor import _get_token_counter

        llm = Mock(spec=[])
        counter = _get_token_counter(llm)
        for text in ["", "x", "hello world", "a" * 10_000]:
            result = counter(text)
            assert isinstance(result, int)
            assert result >= 0
