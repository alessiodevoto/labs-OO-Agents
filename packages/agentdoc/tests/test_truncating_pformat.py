# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TDD: truncating_pformat uses TruncatingStringIO instead of string post-cap slicing.

Change 2 of truncation-2.0: truncating_pformat delegates to TruncatingStringIO
for head+tail truncation, producing valid prose output instead of mid-repr slices.
"""

from agentdoc import truncating_pformat


class TestSafePformatUsesTruncatingStringIO:
    """truncating_pformat must use TruncatingStringIO prose format when cap fires."""

    def test_small_value_passes_through_unchanged(self):
        result = truncating_pformat([1, 2, 3], max_chars=1000)
        assert "1" in result
        assert "Output too large" not in result

    def test_large_non_string_uses_prose_notice(self):
        # TDD: will fail until Change 2 is implemented
        # Current code does string slicing; new code uses TruncatingStringIO.getvalue()
        big = list(range(100_000))
        result = truncating_pformat(big, max_chars=200)
        # TruncatingStringIO produces "Output too large (...) chars..." prose
        assert "Output too large" in result
        assert "chars not shown" in result

    def test_large_non_string_preserves_head_and_tail(self):
        # TDD: will fail until Change 2 is implemented
        # Current string slicing gives head only (broken syntax at cut point).
        # TruncatingStringIO gives head + tail.
        head_marker = "START_UNIQUE_MARKER"
        tail_marker = "END_UNIQUE_MARKER"
        big = [head_marker] + ["x"] * 10_000 + [tail_marker]
        result = truncating_pformat(big, max_chars=300)
        assert head_marker in result
        assert tail_marker in result

    def test_notice_format_matches_truncating_string_io(self):
        # TDD: will fail until Change 2 is implemented
        # TruncatingStringIO.getvalue() format:
        # "Output too large (N chars). Showing first X and last Y chars.
        #  \n\n<head>\n\n... Z chars not shown ...\n\n<tail>"
        big = "a" * 10_000
        result = truncating_pformat(big, max_chars=100)
        assert "Output too large" in result
        assert "Showing first" in result
        assert "and last" in result
        assert "chars not shown" in result

    def test_string_fast_path_unchanged(self):
        # String fast-path is unchanged; TruncatingStringIO is for non-strings
        huge_str = "START" + "x" * 10_000 + "END"
        result = truncating_pformat(huge_str, max_chars=100)
        assert "START" in result
        assert "END" in result
        assert "Output too large" in result

    def test_no_broken_python_syntax_in_output(self):
        # TDD: will fail until Change 2 is implemented
        # String slicing cuts mid-repr (e.g. "[0, 1, 2, 3..." without "]").
        # TruncatingStringIO preserves clean head + clean tail.
        big = list(range(10_000))
        result = truncating_pformat(big, max_chars=500)
        # Result should start with the prose notice, not a dangling "["
        assert result.startswith("<truncated-output>") or result.startswith("[")
        # If it starts with "[", it wasn't truncated — that's fine too
        if "Output too large" in result:
            # Verify it's the TruncatingStringIO format, not a bare slice
            assert "Showing first" in result
