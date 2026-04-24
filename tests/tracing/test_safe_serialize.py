# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for _safe_serialize — bounded span attribute serialization."""

from nemo_oo_agents.tracing._hooks_impl import OpenInferenceHooks


class TestSafeSerialize:
    """_safe_serialize delegates to safe_pformat with a hard cap."""

    def test_small_string_preserved(self):
        result = OpenInferenceHooks._safe_serialize("hello")
        assert result == "hello"

    def test_small_int_preserved(self):
        result = OpenInferenceHooks._safe_serialize(42)
        assert result == "42"

    def test_none_serialized(self):
        result = OpenInferenceHooks._safe_serialize(None)
        assert result == "None"

    def test_dict_serialized(self):
        result = OpenInferenceHooks._safe_serialize({"key": "value"})
        assert "key" in result
        assert "value" in result

    def test_large_string_truncated(self):
        large = "x" * 200_000
        result = OpenInferenceHooks._safe_serialize(large, max_chars=1000)
        assert len(result) < 2000  # cap + notice overhead
        assert "Output too large" in result

    def test_large_object_truncated(self):
        big_list = list(range(100_000))
        result = OpenInferenceHooks._safe_serialize(big_list, max_chars=5000)
        assert len(result) < 10_000  # cap + notice overhead

    def test_default_cap_is_50k(self):
        """Values under 50K are not truncated by default."""
        medium = "y" * 40_000
        result = OpenInferenceHooks._safe_serialize(medium)
        assert result == medium

    def test_broken_repr_does_not_crash(self):
        """Objects with broken __repr__ are handled gracefully."""

        class Bomb:
            def __repr__(self):
                raise RuntimeError("boom")

        result = OpenInferenceHooks._safe_serialize(Bomb())
        assert isinstance(result, str)  # doesn't crash

    def test_pydantic_model(self):
        """Pydantic models are serialized via safe_pformat."""
        from nemo_oo_agents.events import ExecutionResult

        result_obj = ExecutionResult(stdout="hello", stderr="", returned_value=42)
        serialized = OpenInferenceHooks._safe_serialize(result_obj)
        assert "hello" in serialized
        assert "42" in serialized
