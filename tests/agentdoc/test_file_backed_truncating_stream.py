# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for FileBackedTruncatingStringIO."""

import os
import tempfile

import pytest

from nemo_oo_agents.agentdoc._truncating_stream import FileBackedTruncatingStringIO


@pytest.fixture
def buf():
    """Yield a FileBackedTruncatingStringIO and clean up on teardown."""
    b = FileBackedTruncatingStringIO(limit=100)
    yield b
    b.cleanup()


@pytest.fixture
def small_buf():
    """Yield a small-limit buffer for truncation tests."""
    b = FileBackedTruncatingStringIO(limit=10)
    yield b
    b.cleanup()


class TestFileBackedBasicBehavior:
    """Verify it behaves identically to TruncatingStringIO for in-memory operations."""

    def test_small_write_not_truncated(self, buf):
        buf.write("hello")
        assert buf.getvalue() == "hello"
        assert not buf.was_truncated

    def test_write_exactly_at_limit(self, small_buf):
        small_buf.write("x" * 10)
        assert not small_buf.was_truncated
        assert small_buf.getvalue() == "x" * 10

    def test_write_over_limit_truncates(self, small_buf):
        small_buf.write("x" * 100)
        assert small_buf.was_truncated
        assert small_buf.chars_written == 100
        value = small_buf.getvalue()
        assert "Output too large" in value
        assert "100 chars" in value

    def test_multiple_writes_accumulate(self, buf):
        buf.write("abc")
        buf.write("def")
        assert buf.getvalue() == "abcdef"
        assert buf.chars_written == 6

    def test_empty_write(self, buf):
        buf.write("")
        assert buf.getvalue() == ""
        assert not buf.was_truncated

    def test_custom_tail_chars(self):
        buf = FileBackedTruncatingStringIO(limit=20, tail_chars=15)
        try:
            buf.write("A" * 5)  # head_limit = 5
            buf.write("B" * 100)  # tail keeps last 15
            assert buf.was_truncated
            value = buf.getvalue()
            assert value.count("B") == 15
        finally:
            buf.cleanup()

    def test_unicode_round_trip(self, buf):
        text = "Hello 世界! 🚀 café"
        buf.write(text)
        assert buf.getvalue() == text
        with open(buf.file_path) as f:
            assert f.read() == text


class TestFileBackedFileOutput:
    """Verify file-backed behavior: full output written to temp file."""

    def test_file_created(self, buf):
        buf.write("hello")
        assert buf.file_path is not None
        assert os.path.exists(buf.file_path)

    def test_file_contains_full_output_when_truncated(self, small_buf):
        content = "A" * 5 + "B" * 90 + "C" * 5
        small_buf.write(content)
        assert small_buf.was_truncated
        with open(small_buf.file_path) as f:
            assert f.read() == content

    def test_file_contains_full_output_when_not_truncated(self, buf):
        buf.write("hello world")
        with open(buf.file_path) as f:
            assert f.read() == "hello world"

    def test_file_contains_multiple_writes(self, small_buf):
        small_buf.write("hello ")
        small_buf.write("world ")
        small_buf.write("this is a long string")
        with open(small_buf.file_path) as f:
            assert f.read() == "hello world this is a long string"

    def test_custom_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = FileBackedTruncatingStringIO(limit=100, dir=tmpdir)
            try:
                buf.write("test")
                assert buf.file_path.startswith(tmpdir)
            finally:
                buf.cleanup()

    def test_custom_prefix_suffix(self):
        buf = FileBackedTruncatingStringIO(limit=100, prefix="myprefix_", suffix=".log")
        try:
            buf.write("test")
            basename = os.path.basename(buf.file_path)
            assert basename.startswith("myprefix_")
            assert basename.endswith(".log")
        finally:
            buf.cleanup()

    def test_large_output(self):
        """Verify file correctness with substantial output."""
        buf = FileBackedTruncatingStringIO(limit=1000)
        try:
            content = "x" * 50_000
            buf.write(content)
            assert buf.was_truncated
            with open(buf.file_path) as f:
                assert f.read() == content
        finally:
            buf.cleanup()


class TestFileBackedTruncationNotice:
    """Verify truncation notice includes file path."""

    def test_truncation_notice_includes_file_path(self, small_buf):
        small_buf.write("x" * 100)
        value = small_buf.getvalue()
        assert small_buf.file_path in value
        assert "Full output saved to:" in value

    def test_truncation_notice_has_standard_format(self, small_buf):
        small_buf.write("x" * 100)
        value = small_buf.getvalue()
        assert value.startswith("<truncated-output>")
        assert "Output too large" in value
        assert "100 chars" in value
        assert "not shown" in value

    def test_not_truncated_no_file_path_in_output(self, buf):
        buf.write("hello")
        value = buf.getvalue()
        assert value == "hello"
        assert "saved to" not in value

    def test_head_and_tail_preserved(self):
        buf = FileBackedTruncatingStringIO(limit=50)
        try:
            buf.write("HEADDATA" + "x" * 200 + "TAILCONTENT")
            value = buf.getvalue()
            assert "HEADDATA" in value
            assert "TAILCONTENT" in value
            assert buf.file_path in value
        finally:
            buf.cleanup()

    def test_getvalue_idempotent(self, small_buf):
        """getvalue() should return the same result on repeated calls."""
        small_buf.write("x" * 100)
        v1 = small_buf.getvalue()
        v2 = small_buf.getvalue()
        assert v1 == v2


class TestFileBackedErrorHandling:
    """Verify graceful fallback when file I/O fails."""

    def test_fallback_on_invalid_dir(self):
        buf = FileBackedTruncatingStringIO(limit=10, dir="/nonexistent/path/xyz")
        try:
            buf.write("x" * 100)
            assert buf.was_truncated
            value = buf.getvalue()
            assert "Output too large" in value
            # No file path in notice since file creation failed
            assert "Full output saved to:" not in value
        finally:
            buf.cleanup()

    def test_fallback_still_counts_chars(self):
        buf = FileBackedTruncatingStringIO(limit=10, dir="/nonexistent/path/xyz")
        try:
            buf.write("x" * 50)
            assert buf.chars_written == 50
        finally:
            buf.cleanup()

    def test_file_path_none_on_failure(self):
        buf = FileBackedTruncatingStringIO(limit=10, dir="/nonexistent/path/xyz")
        try:
            assert buf.file_path is None
        finally:
            buf.cleanup()


class TestFileBackedCleanup:
    """Verify close() and cleanup behavior."""

    def test_close_closes_file_but_keeps_on_disk(self, buf):
        buf.write("test")
        path = buf.file_path
        buf.close()
        assert os.path.exists(path)
        # cleanup still needed
        os.unlink(path)

    def test_cleanup_removes_file(self, buf):
        buf.write("test")
        path = buf.file_path
        buf.cleanup()
        assert not os.path.exists(path)

    def test_cleanup_idempotent(self, buf):
        buf.write("test")
        buf.cleanup()
        buf.cleanup()  # Should not raise

    def test_close_idempotent(self, buf):
        buf.write("test")
        buf.close()
        buf.close()  # Should not raise

    def test_getvalue_after_close(self, small_buf):
        """getvalue() works after close() — reads from in-memory buffers."""
        small_buf.write("x" * 100)
        small_buf.close()
        value = small_buf.getvalue()
        assert "Output too large" in value
        assert small_buf.file_path in value

    def test_cleanup_then_getvalue(self, small_buf):
        """After cleanup the file is gone; notice should still render."""
        small_buf.write("x" * 100)
        path = small_buf.file_path
        small_buf.cleanup()
        # getvalue still works (in-memory data intact), notice may reference deleted file
        value = small_buf.getvalue()
        assert "Output too large" in value


class TestFileBackedExport:
    """Verify the class is properly exported."""

    def test_importable_from_agentdoc(self):
        from nemo_oo_agents.agentdoc import FileBackedTruncatingStringIO as FB
        assert FB is FileBackedTruncatingStringIO
