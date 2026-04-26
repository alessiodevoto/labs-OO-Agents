# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``flush_pending`` -- the journal callback's daemon-thread join.

The eval pipeline's process-exit race truncated journal POSTs at the
receiver because ``_post_json`` dispatches into daemon threads and the
calling process can exit before they finish.  ``JournalExporter.force_flush``
calls ``flush_pending`` to block on those threads.  This module covers
the join behaviour directly, including the append-vs-start race that
the original implementation got wrong.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from nemo_oo_agents.tracing._litellm_journal import (
    _PENDING_THREADS,
    _post_json,
    flush_pending,
)


def test_flush_pending_blocks_until_post_completes():
    """``flush_pending`` must not return until in-flight POSTs have run
    their HTTP request to completion.  Otherwise the eval-pipeline
    process-exit race comes back."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_post(*_args, **_kwargs):
        started.set()
        release.wait(timeout=10)
        finished.set()

    with patch(
        "nemo_oo_agents.tracing._litellm_journal.urllib.request.urlopen",
        side_effect=slow_post,
    ):
        _post_json("http://example.invalid/v1/journal/calls", {"call_id": "x"})
        assert started.wait(timeout=2), "POST thread didn't start"

        # Run flush_pending in another thread so we can observe it block.
        flushed = threading.Event()

        def _flush():
            flush_pending(timeout=10)
            flushed.set()

        flush_thread = threading.Thread(target=_flush, daemon=True)
        flush_thread.start()
        # If it returned immediately, the test infra is broken.
        assert not flushed.wait(timeout=0.2), (
            "flush_pending returned before the POST thread finished"
        )

        release.set()
        assert flushed.wait(timeout=5), "flush_pending didn't return after POST done"
        assert finished.is_set()


def test_flush_pending_handles_thread_added_during_flush():
    """A POST dispatched *while* ``flush_pending`` is mid-join must still
    be waited on -- otherwise back-to-back log_success_event calls drop
    the second batch.
    """
    barrier = threading.Event()
    seen: list[str] = []

    def post1(*_args, **_kwargs):
        # Block briefly so a second post can be dispatched while flush is
        # joining the first.
        barrier.wait(timeout=5)
        seen.append("p1")

    def post2(*_args, **_kwargs):
        seen.append("p2")

    # Patch urlopen with a side-effect that dispatches a second POST when
    # the first one starts -- mimicking a real flow where POSTs queue up.
    posted_second = threading.Event()

    def queue_first(*args, **kwargs):
        if not posted_second.is_set():
            posted_second.set()
            _post_json("http://example.invalid/two", {"x": 2})
        post1(*args, **kwargs)

    with patch(
        "nemo_oo_agents.tracing._litellm_journal.urllib.request.urlopen",
        side_effect=queue_first,
    ):
        _post_json("http://example.invalid/one", {"x": 1})
        # Give post1 a moment to start so it's the active thread.
        time.sleep(0.1)
        # Release post1; it appends to seen, then post2 (already queued)
        # runs and appends.  flush_pending should wait for both.
        barrier.set()
        flush_pending(timeout=5)

    assert "p1" in seen
    assert posted_second.is_set()


def test_pending_threads_self_evict_when_post_returns():
    """``_PENDING_THREADS`` must not grow without bound: the worker thread
    discards itself from the set when it returns, so a long-running
    process that never calls ``flush_pending`` doesn't leak."""

    def fast_post(*_args, **_kwargs):
        return None

    with patch(
        "nemo_oo_agents.tracing._litellm_journal.urllib.request.urlopen",
        side_effect=fast_post,
    ):
        for i in range(5):
            _post_json(f"http://example.invalid/{i}", {"i": i})

        # Wait for all threads to drain.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _PENDING_THREADS:
            time.sleep(0.02)
        assert not _PENDING_THREADS, (
            f"_PENDING_THREADS leaked entries after completion: {_PENDING_THREADS!r}"
        )
