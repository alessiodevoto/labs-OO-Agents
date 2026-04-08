"""Tests for _safe_serialize sentinel handling."""

import json

from openinference_instrumentation_nemo_oo_agents._hooks_impl import OpenInferenceHooks


class TestSafeSerializeSentinel:
    """Ensure bare object() sentinels don't leak into trace output."""

    def setup_method(self):
        self.hooks = OpenInferenceHooks.__new__(OpenInferenceHooks)

    def test_sentinel_in_pydantic_model_becomes_null(self):
        """ExecutionResult with _NO_RETURN sentinel serializes returned_value as null."""
        from nemo_oo_agents.events import ExecutionResult

        result = ExecutionResult(stdout="hello", stderr="")
        assert not result.has_return  # default is _NO_RETURN sentinel

        serialized = self.hooks._safe_serialize(result)
        parsed = json.loads(serialized)

        # returned_value should be null, not "<object object at 0x...>"
        assert parsed.get("returned_value") is None

    def test_real_return_value_preserved(self):
        """ExecutionResult with a real return value serializes correctly."""
        from nemo_oo_agents.events import ExecutionResult

        result = ExecutionResult(stdout="", stderr="", returned_value=42)
        assert result.has_return

        serialized = self.hooks._safe_serialize(result)
        parsed = json.loads(serialized)

        assert parsed["returned_value"] == 42

    def test_string_return_value_preserved(self):
        """String return values are not mistakenly filtered."""
        from nemo_oo_agents.events import ExecutionResult

        result = ExecutionResult(stdout="", stderr="", returned_value="hello world")
        serialized = self.hooks._safe_serialize(result)
        parsed = json.loads(serialized)

        assert parsed["returned_value"] == "hello world"

    def test_falsy_return_values_preserved(self):
        """Falsy values (False, 0, empty string) are not mistakenly filtered."""
        from nemo_oo_agents.events import ExecutionResult

        for value in [False, 0, 0.0, ""]:
            result = ExecutionResult(stdout="", stderr="", returned_value=value)
            serialized = self.hooks._safe_serialize(result)
            parsed = json.loads(serialized)
            assert parsed["returned_value"] == value, f"Falsy value {value!r} was incorrectly filtered"

    def test_none_return_excluded_by_model_dump(self):
        """returned_value=None is excluded by model_dump(exclude_none=True)."""
        from nemo_oo_agents.events import ExecutionResult

        result = ExecutionResult(stdout="", stderr="", returned_value=None)
        serialized = self.hooks._safe_serialize(result)
        parsed = json.loads(serialized)
        # exclude_none=True strips None fields, so returned_value won't be in dict
        assert "returned_value" not in parsed


class TestSafeSerializeNoTruncation:
    """Traces must NOT truncate — _safe_serialize is always lossless.

    Agent-facing truncation is done upstream (safe_pformat / block-level limits).
    By the time a value reaches a span attribute it is already the bounded
    representation the agent actually saw — we must not truncate it further.
    """

    def setup_method(self):
        self.hooks = OpenInferenceHooks.__new__(OpenInferenceHooks)

    def test_large_string_is_not_truncated(self):
        """A large string is serialised in full — traces are lossless."""
        large = "x" * 100_000
        result = self.hooks._safe_serialize(large)
        assert result == large  # exact preservation, no extra chars

    def test_max_length_param_accepted_but_ignored(self):
        """max_length is accepted for API compatibility but has no effect."""
        large = "y" * 50_000
        result_no_limit = self.hooks._safe_serialize(large)
        result_with_limit = self.hooks._safe_serialize(large, max_length=100)
        # Both must produce the same output — max_length is a no-op
        assert result_no_limit == result_with_limit
