# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for TruncationConfig model_validator — catches bad values on construction."""

import pytest

from nemo_oo_agents.config.truncation_config import TruncationConfig


class TestTruncationConfigValidation:
    def test_default_config_is_valid(self):
        TruncationConfig()  # must not raise

    def test_rejects_zero_max_block_chars(self):
        with pytest.raises(ValueError, match="max_block_chars"):
            TruncationConfig(max_block_chars=0)

    def test_rejects_negative_max_stdout_chars(self):
        with pytest.raises(ValueError, match="max_stdout_chars"):
            TruncationConfig(max_stdout_chars=-1)

    def test_rejects_zero_max_stderr_chars(self):
        with pytest.raises(ValueError, match="max_stderr_chars"):
            TruncationConfig(max_stderr_chars=0)

    def test_rejects_zero_max_pre_format_chars(self):
        with pytest.raises(ValueError, match="max_pre_format_chars"):
            TruncationConfig(max_pre_format_chars=0)

    def test_rejects_zero_pprint_elements(self):
        with pytest.raises(ValueError, match="max_pprint_elements"):
            TruncationConfig(max_pprint_elements=0)

    def test_rejects_zero_pprint_string(self):
        with pytest.raises(ValueError, match="max_pprint_string"):
            TruncationConfig(max_pprint_string=0)

    def test_rejects_zero_pprint_depth(self):
        with pytest.raises(ValueError, match="max_pprint_depth"):
            TruncationConfig(max_pprint_depth=0)

    def test_allows_none_pprint_limits(self):
        TruncationConfig(max_pprint_elements=None, max_pprint_string=None, max_pprint_depth=None)

    def test_rejects_negative_stdout_tail_chars(self):
        with pytest.raises(ValueError, match="stdout_tail_chars"):
            TruncationConfig(stdout_tail_chars=-1)

    def test_rejects_stdout_tail_chars_gte_max_stdout(self):
        with pytest.raises(ValueError, match="stdout_tail_chars"):
            TruncationConfig(max_stdout_chars=1000, stdout_tail_chars=1000)

    def test_rejects_stdout_tail_chars_gte_max_stderr(self):
        with pytest.raises(ValueError, match="stdout_tail_chars"):
            TruncationConfig(max_stderr_chars=500, stdout_tail_chars=500)

    def test_allows_valid_stdout_tail_chars(self):
        cfg = TruncationConfig(max_stdout_chars=1000, max_stderr_chars=500, stdout_tail_chars=200)
        assert cfg.stdout_tail_chars == 200

    def test_collects_multiple_errors(self):
        """All errors reported together, not one at a time."""
        with pytest.raises(ValueError) as exc_info:
            TruncationConfig(max_block_chars=0, max_stdout_chars=-1)
        msg = str(exc_info.value)
        assert "max_block_chars" in msg
        assert "max_stdout_chars" in msg

    def test_rejects_zero_context_tokens(self):
        with pytest.raises(ValueError, match="max_context_tokens"):
            TruncationConfig(max_context_tokens=0)

    def test_allows_none_token_limits(self):
        TruncationConfig(max_context_tokens=None, max_event_tokens=None)
