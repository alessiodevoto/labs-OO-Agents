# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TDD: tail(N) context manager for capturing last N chars of stdout.

Change 3 of truncation-2.0: tail() uses ContextVar mechanism (not redirect_stdout)
to temporarily capture stdout into a tail-only TruncatingStringIO buffer.
"""

import pytest

from nemo_oo_agents.runtime.truncating_stream import TruncatingStringIO


class TestTailContextManager:
    """tail() must capture last N chars via ContextVar, not sys.stdout swap."""

    def _make_outer_buffer(self):
        return TruncatingStringIO(limit=100_000)

    def test_tail_is_importable(self):
        # TDD: will fail until Change 3 is implemented
        from nemo_oo_agents.runtime.stream_wrappers import tail
        assert callable(tail)

    def test_tail_forwards_output_to_outer_buffer(self):
        # TDD: will fail until Change 3 is implemented
        from nemo_oo_agents.runtime.stream_wrappers import tail, _stdout_buffer_var

        outer = self._make_outer_buffer()
        token = _stdout_buffer_var.set(outer)
        try:
            with tail(100):
                _stdout_buffer_var.get().write("hello from tail\n")
            assert "hello from tail" in outer.getvalue()
        finally:
            _stdout_buffer_var.reset(token)

    def test_tail_only_keeps_last_n_chars(self):
        # TDD: will fail until Change 3 is implemented
        from nemo_oo_agents.runtime.stream_wrappers import tail, _stdout_buffer_var

        outer = self._make_outer_buffer()
        token = _stdout_buffer_var.set(outer)
        try:
            with tail(10):
                buf = _stdout_buffer_var.get()
                buf.write("A" * 500)  # 500 chars, only last 10 kept
            result = outer.getvalue()
            # Only last 10 chars of "AAA...A" should be forwarded
            assert len(result) <= 50  # 10 chars + some overhead
        finally:
            _stdout_buffer_var.reset(token)

    def test_tail_restores_outer_buffer_on_exit(self):
        # TDD: will fail until Change 3 is implemented
        from nemo_oo_agents.runtime.stream_wrappers import tail, _stdout_buffer_var

        outer = self._make_outer_buffer()
        token = _stdout_buffer_var.set(outer)
        try:
            with tail(100):
                inner_buf = _stdout_buffer_var.get()
                assert inner_buf is not outer  # different buffer inside
            # After exit, should be restored to outer
            assert _stdout_buffer_var.get() is outer
        finally:
            _stdout_buffer_var.reset(token)

    def test_tail_restores_buffer_on_exception(self):
        # TDD: will fail until Change 3 is implemented
        from nemo_oo_agents.runtime.stream_wrappers import tail, _stdout_buffer_var

        outer = self._make_outer_buffer()
        token = _stdout_buffer_var.set(outer)
        try:
            try:
                with tail(100):
                    raise ValueError("test error")
            except ValueError:
                pass
            assert _stdout_buffer_var.get() is outer  # restored despite exception
        finally:
            _stdout_buffer_var.reset(token)

    def test_tail_uses_head_limit_zero(self):
        # TDD: will fail until Change 3 is implemented
        # TruncatingStringIO(limit=N, tail_chars=N) → head_limit=0 → all to tail
        from nemo_oo_agents.runtime.stream_wrappers import tail, _stdout_buffer_var

        outer = self._make_outer_buffer()
        token = _stdout_buffer_var.set(outer)
        try:
            with tail(20):
                buf = _stdout_buffer_var.get()
                buf.write("FIRST_PART_DISCARDED" + "LAST20CHARS_KEPT")
            # Only last 20 chars should appear in outer
            result = outer.getvalue()
            assert "LAST20CHARS_KEPT" in result
            assert "FIRST_PART" not in result
        finally:
            _stdout_buffer_var.reset(token)

    def test_tail_with_no_outer_buffer_does_not_crash(self):
        # TDD: will fail until Change 3 is implemented
        from nemo_oo_agents.runtime.stream_wrappers import tail, _stdout_buffer_var

        # No outer buffer set
        token = _stdout_buffer_var.set(None)
        try:
            with tail(100):
                buf = _stdout_buffer_var.get()
                if buf:
                    buf.write("test")
            # Should not crash when outer buffer is None
        finally:
            _stdout_buffer_var.reset(token)
