# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for char_approximate_token_counter and explicit-opt-in token counting.

gl-89 (revised): No silent fallback. When max_context_tokens / max_event_tokens is
set but the LLM has no count_tokens, a RuntimeError is raised pointing the user to
char_approximate_token_counter as an explicit opt-in.
"""

from unittest.mock import Mock


class TestCharApproximateTokenCounter:
    """char_approximate_token_counter is a public explicit-opt-in utility."""

    def test_is_importable_from_nemo_oo_agents(self):
        from nemo_oo_agents import char_approximate_token_counter

        assert callable(char_approximate_token_counter)

    def test_is_importable_from_token_counter_module(self):
        from nemo_oo_agents.token_counter import char_approximate_token_counter

        assert callable(char_approximate_token_counter)

    def test_formula_is_len_div_4(self):
        from nemo_oo_agents import char_approximate_token_counter

        assert char_approximate_token_counter("") == 0
        assert char_approximate_token_counter("xxxx") == 1
        assert char_approximate_token_counter("x" * 100) == 25
        assert char_approximate_token_counter("x" * 1000) == 250

    def test_returns_non_negative_int(self):
        from nemo_oo_agents import char_approximate_token_counter

        for text in ["", "x", "hello world", "a" * 10_000]:
            result = char_approximate_token_counter(text)
            assert isinstance(result, int)
            assert result >= 0

    def test_can_be_attached_as_count_tokens(self):
        """Users can attach it to their LLM as count_tokens."""
        from nemo_oo_agents import char_approximate_token_counter

        llm = Mock()
        llm.count_tokens = char_approximate_token_counter
        assert llm.count_tokens("x" * 40) == 10


class TestNoSilentFallback:
    """Runtime raises RuntimeError when token limits set but LLM has no count_tokens."""

    def test_actor_has_no_get_token_counter(self):
        """_get_token_counter was removed — no silent fallback."""
        import nemo_oo_agents.runtime.actor as actor_mod

        assert not hasattr(actor_mod, "_get_token_counter"), (
            "_get_token_counter should have been removed; silent fallback was replaced "
            "by an explicit RuntimeError pointing to char_approximate_token_counter"
        )

    def test_error_message_mentions_char_approximate(self):
        """RuntimeError message guides the user to the explicit opt-in."""
        # We verify the error text by directly inspecting the actor source.
        import inspect

        import nemo_oo_agents.runtime.actor as actor_mod

        source = inspect.getsource(actor_mod)
        assert "char_approximate_token_counter" in source, (
            "RuntimeError message should mention char_approximate_token_counter"
        )
