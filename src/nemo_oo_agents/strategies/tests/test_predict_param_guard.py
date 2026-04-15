# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for PredictStrategy parameter size guard.

PredictStrategy is single-shot — a silently truncated input produces wrong output.
Oversized params must raise ValueError with a clear message rather than truncating.
"""

import pytest

from nemo_oo_agents.config.strategy_config import PredictConfig
from nemo_oo_agents.strategies.predict import PredictStrategy
from nemo_oo_agents.strategies.current_call import CurrentCall


def _make_call(args=(), kwargs=None, signature=None):
    return CurrentCall(
        id="test-id",
        method_name="test",
        decorator="agent",
        signature=signature,
        args=args,
        kwargs=kwargs or {},
    )


class TestPredictParamGuard:
    """_assert_param_sizes raises ValueError for oversized parameters."""

    def _strategy(self, max_param_chars=200_000):
        return PredictStrategy(config=PredictConfig(max_param_chars=max_param_chars))

    def test_small_param_passes(self):
        strategy = self._strategy()
        call = _make_call(args=("hello",), signature="(text: str)")
        strategy._assert_param_sizes(call)  # must not raise

    def test_small_kwarg_passes(self):
        strategy = self._strategy()
        call = _make_call(kwargs={"data": list(range(10))})
        strategy._assert_param_sizes(call)  # must not raise

    def test_oversized_string_raises(self):
        strategy = self._strategy(max_param_chars=1000)
        call = _make_call(args=("x" * 2000,), signature="(text: str)")
        with pytest.raises(ValueError, match="text"):
            strategy._assert_param_sizes(call)

    def test_oversized_list_raises(self):
        strategy = self._strategy(max_param_chars=500)
        call = _make_call(args=(list(range(10_000)),), signature="(items: list)")
        with pytest.raises(ValueError, match="items"):
            strategy._assert_param_sizes(call)

    def test_error_message_names_the_param(self):
        strategy = self._strategy(max_param_chars=100)
        call = _make_call(
            args=("y" * 500,),
            signature="(document: str)",
        )
        with pytest.raises(ValueError, match="document"):
            strategy._assert_param_sizes(call)

    def test_error_message_mentions_max_param_chars(self):
        strategy = self._strategy(max_param_chars=500)
        call = _make_call(kwargs={"report": "z" * 2000})
        with pytest.raises(ValueError, match="500"):
            strategy._assert_param_sizes(call)

    def test_error_message_suggests_raising_limit(self):
        strategy = self._strategy(max_param_chars=100)
        call = _make_call(kwargs={"x": "a" * 500})
        with pytest.raises(ValueError, match="max_param_chars"):
            strategy._assert_param_sizes(call)

    def test_large_default_allows_document_summarization(self):
        """Default 200K chars covers realistic documents without raising."""
        strategy = self._strategy()  # default 200_000
        # Typical long document: ~150K chars
        call = _make_call(
            args=("word " * 30_000,),  # ~150K chars
            signature="(document: str)",
        )
        strategy._assert_param_sizes(call)  # must not raise

    def test_no_signature_uses_arg_index_names(self):
        """Without a signature, oversized positional arg raises with arg_0 name."""
        strategy = self._strategy(max_param_chars=100)
        call = _make_call(args=("b" * 500,))
        with pytest.raises(ValueError, match="arg_0"):
            strategy._assert_param_sizes(call)

    def test_custom_limit_respected(self):
        """PredictConfig(max_param_chars=N) sets the limit."""
        strategy = self._strategy(max_param_chars=50)
        call = _make_call(args=("c" * 100,), signature="(text: str)")
        with pytest.raises(ValueError):
            strategy._assert_param_sizes(call)

        # With a higher limit, same call passes
        strategy2 = self._strategy(max_param_chars=200)
        strategy2._assert_param_sizes(call)  # must not raise
