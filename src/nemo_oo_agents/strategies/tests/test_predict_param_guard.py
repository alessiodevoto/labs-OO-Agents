# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for PredictStrategy parameter size guard.

PredictStrategy is single-shot — a silently truncated input produces wrong output.
Oversized params must raise ValueError with a clear message rather than truncating.
"""

import pytest

from nemo_oo_agents.config.strategy_config import PredictConfig
from nemo_oo_agents.strategies.current_call import CurrentCall
from nemo_oo_agents.strategies.predict import PredictStrategy


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


class TestPredictPromptSizeGuard:
    """PredictStrategy.execute raises when task prompt would be silently truncated.

    max_block_chars caps how much of the Task event the LLM sees.  If the built
    task prompt exceeds this limit, the input is silently cropped — PredictStrategy
    must detect this and raise ValueError before adding the event.
    """

    @staticmethod
    def _make_mock_runtime(max_block_chars: int):
        """Create a full MockRuntime satisfying RuntimeServices with a custom truncation cap."""
        from unittest.mock import MagicMock

        from nemo_oo_agents.config.truncation_config import TruncationConfig

        _tc = TruncationConfig(max_block_chars=max_block_chars)

        class _MockRuntime:
            @property
            def agent(self):
                return MagicMock()

            @property
            def event_manager(self):
                return MagicMock()

            @property
            def truncation_config(self):
                return _tc

            async def generate(self, *, tools=None, output_model=None, **kwargs):
                raise NotImplementedError("generate() not needed for these tests")

            async def execute_code(self, code, *, builtins=None, validate=True, **kwargs):
                raise NotImplementedError("execute_code() not needed for these tests")

            async def execute_nested(self, strat, call):
                return await strat.execute(self, call)

            def get_generation_id(self):
                return "mock-id"

            def get_parent_generation_id(self):
                return None

            async def expand_variables(self, template, extra_context=None, error_mode="show"):
                import string

                context = extra_context or {}
                formatter = string.Formatter()
                result_parts = []
                for literal_text, field_name, format_spec, conversion in formatter.parse(template):
                    result_parts.append(literal_text)
                    if field_name is not None:
                        try:
                            value = eval(field_name, {}, context)  # noqa: S307
                            if conversion == "r":
                                value = repr(value)
                            elif conversion == "s":
                                value = str(value)
                            result_parts.append(
                                format(value, format_spec) if format_spec else str(value)
                            )
                        except Exception as e:
                            if error_mode == "raise":
                                raise
                            result_parts.append(f"{{{field_name} | ERROR: {e}}}")
                return "".join(result_parts)

        return _MockRuntime()

    @staticmethod
    def _make_call(docstring: str, kwargs: dict):
        from nemo_oo_agents.strategies.current_call import CurrentCall

        return CurrentCall(
            id="test",
            method_name="test_method",
            decorator="agent",
            docstring=docstring,
            args=(),
            kwargs=kwargs,
            return_type=str,  # PredictStrategy requires a return type
        )

    @pytest.mark.asyncio
    async def test_oversized_prompt_raises_before_adding_event(self):
        """Task prompt exceeding max_block_chars must raise ValueError with clear message."""
        big_docstring = "Analyze the following. " + "x" * 2000
        call = self._make_call(big_docstring, {})
        runtime = self._make_mock_runtime(max_block_chars=100)  # Too small

        strategy = PredictStrategy()
        with pytest.raises(ValueError, match="silently truncated") as exc_info:
            await strategy.execute(runtime, call)

        error_msg = str(exc_info.value)
        assert "test_method" in error_msg
        assert "max_block_chars" in error_msg
        assert "TruncationConfig" in error_msg

    @pytest.mark.asyncio
    async def test_error_message_names_the_method(self):
        """Error message must include the method name."""
        call = self._make_call("x" * 5000, {})
        runtime = self._make_mock_runtime(max_block_chars=100)

        strategy = PredictStrategy()
        with pytest.raises(ValueError, match="test_method"):
            await strategy.execute(runtime, call)

    @pytest.mark.asyncio
    async def test_error_message_mentions_max_block_chars_value(self):
        """Error message must include the max_block_chars value."""
        call = self._make_call("x" * 5000, {})
        runtime = self._make_mock_runtime(max_block_chars=750)

        strategy = PredictStrategy()
        with pytest.raises(ValueError, match="750"):
            await strategy.execute(runtime, call)

    @pytest.mark.asyncio
    async def test_small_prompt_passes_the_size_check(self):
        """Task prompt within max_block_chars should NOT trigger the truncation guard.

        The method will still fail later (no LLM), but NOT with the truncation guard error.
        """
        call = self._make_call("Classify the following text.", {"text": "hello world"})
        runtime = self._make_mock_runtime(max_block_chars=100_000)

        strategy = PredictStrategy()
        with pytest.raises(Exception) as exc_info:
            await strategy.execute(runtime, call)
        # The error must NOT be our truncation guard
        assert "silently truncated" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_error_suggests_exact_limit_to_use(self):
        """Error message should suggest a specific TruncationConfig value."""
        call = self._make_call("x" * 500, {})
        runtime = self._make_mock_runtime(max_block_chars=50)

        strategy = PredictStrategy()
        with pytest.raises(ValueError) as exc_info:
            await strategy.execute(runtime, call)

        # Should mention TruncationConfig with max_block_chars= in the suggestion
        assert "max_block_chars=" in str(exc_info.value)
