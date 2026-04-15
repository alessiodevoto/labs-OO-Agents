# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TDD: gl-74 — _safe_serialize_execution_result caps returned_value before JSON.

Change 4 of truncation-2.0: Use safe_pformat(rv, max_chars=50_000) and remove
the post-JSON string slicing that produces invalid JSON on large return values.
"""

import json

from openinference_instrumentation_nemo_oo_agents._hooks_impl import OpenInferenceHooks


class TestGl74ReturnedValueCap:
    """_safe_serialize_execution_result must cap returned_value, produce valid JSON."""

    def setup_method(self):
        self.hooks = OpenInferenceHooks.__new__(OpenInferenceHooks)

    def _make_result(self, returned_value):
        from nemo_oo_agents.events import ExecutionResult
        return ExecutionResult(stdout="", stderr="", returned_value=returned_value)

    def test_small_return_value_serializes_to_valid_json(self):
        result = self._make_result({"answer": 42})
        serialized = self.hooks._safe_serialize_execution_result(result)
        parsed = json.loads(serialized)
        assert isinstance(parsed, dict)

    def test_large_string_return_value_is_capped(self):
        # TDD: will fail until Change 4 is implemented
        huge = "x" * 200_000
        result = self._make_result(huge)
        serialized = self.hooks._safe_serialize_execution_result(result)
        parsed = json.loads(serialized)  # Must be valid JSON
        rv = parsed.get("returned_value", "")
        assert len(rv) <= 55_000  # 50K cap + notice overhead

    def test_large_list_return_value_is_capped(self):
        # TDD: will fail until Change 4 is implemented
        big = list(range(100_000))
        result = self._make_result(big)
        serialized = self.hooks._safe_serialize_execution_result(result)
        parsed = json.loads(serialized)
        rv = parsed.get("returned_value", "")
        assert len(rv) <= 55_000

    def test_capped_output_uses_prose_notice(self):
        # TDD: will fail until Change 4 is implemented
        # safe_pformat produces "Output too large..." prose, not raw s[:50000]
        huge = "START" + "x" * 200_000 + "END"
        result = self._make_result(huge)
        serialized = self.hooks._safe_serialize_execution_result(result)
        parsed = json.loads(serialized)
        rv = parsed.get("returned_value", "")
        assert "Output too large" in rv
        assert "START" in rv  # head preserved
        assert "END" in rv    # tail preserved

    def test_post_json_slicing_gone_output_is_valid_json(self):
        # TDD: will fail until Change 4 is implemented
        # Old code did s[:50000] on the JSON string — breaks JSON syntax.
        # New code caps returned_value before serialization → always valid JSON.
        huge = list(range(500_000))
        result = self._make_result(huge)
        serialized = self.hooks._safe_serialize_execution_result(result)
        # Must parse without error
        parsed = json.loads(serialized)
        assert isinstance(parsed, dict)

    def test_none_returned_value_not_in_output(self):
        from nemo_oo_agents.events import ExecutionResult
        result = ExecutionResult(stdout="", stderr="")
        serialized = self.hooks._safe_serialize_execution_result(result)
        parsed = json.loads(serialized)
        assert "returned_value" not in parsed or parsed["returned_value"] is None
