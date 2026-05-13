"""Unit tests for adaptive archival logic in the context window safety net.

Tests the computation that determines how many events to archive
when the safety net drops messages.
"""

import math

from nemo_oo_agents.runtime.actor import _ARCHIVE_TARGET_UTILIZATION


class TestAdaptiveArchivalComputation:
    """Test the adaptive archival n_to_archive calculation."""

    def _compute_n_to_archive(self, *, cap, total_tok, events_tok, n_active, dropped):
        """Replicate the archival computation from actor.py."""
        target_tok = int(cap * _ARCHIVE_TARGET_UTILIZATION)
        tokens_to_shed = max(0, total_tok - target_tok)
        n_represented = max(1, n_active - dropped)
        avg_event_tok = events_tok / n_represented
        n_to_archive = min(
            int(math.ceil(tokens_to_shed / max(1, avg_event_tok))),
            n_active,
        )
        return n_to_archive

    def test_basic_archival_count(self):
        """Basic case: over budget, archives proportionally."""
        # cap=1000, target=600, total_tok=800 → tokens_to_shed=200
        # 10 events, 0 dropped → avg=500/10=50 per event → need 4
        n = self._compute_n_to_archive(
            cap=1000, total_tok=800, events_tok=500, n_active=10, dropped=0
        )
        assert n == 4

    def test_under_target_no_archival(self):
        """When total_tok <= target, no archival needed."""
        n = self._compute_n_to_archive(
            cap=1000, total_tok=500, events_tok=400, n_active=10, dropped=0
        )
        assert n == 0

    def test_exactly_at_target(self):
        """When total_tok == target, no archival needed."""
        n = self._compute_n_to_archive(
            cap=1000, total_tok=600, events_tok=500, n_active=10, dropped=0
        )
        assert n == 0

    def test_n_active_zero(self):
        """Edge case: no active events — cannot archive."""
        n = self._compute_n_to_archive(cap=1000, total_tok=800, events_tok=0, n_active=0, dropped=0)
        assert n == 0

    def test_events_tok_zero(self):
        """Edge case: events_tok=0 means avg is 0, capped to archive all."""
        # tokens_to_shed=200, avg=0 → would be infinite without max(1,...) guard
        # ceil(200/1)=200, but min(200, 5)=5
        n = self._compute_n_to_archive(cap=1000, total_tok=800, events_tok=0, n_active=5, dropped=0)
        assert n == 5  # capped at n_active

    def test_capped_at_n_active(self):
        """n_to_archive never exceeds n_active."""
        n = self._compute_n_to_archive(
            cap=1000, total_tok=950, events_tok=50, n_active=3, dropped=0
        )
        assert n <= 3

    def test_denominator_uses_n_represented(self):
        """With dropped messages, avg_event_tok uses (n_active - dropped)."""
        # 10 active, 3 dropped → n_represented=7
        # events_tok=700 → avg=100 per event
        # cap=1000, target=600, total_tok=900 → tokens_to_shed=300
        # n_to_archive = ceil(300/100) = 3
        n = self._compute_n_to_archive(
            cap=1000, total_tok=900, events_tok=700, n_active=10, dropped=3
        )
        assert n == 3

    def test_denominator_without_dropped_fix_over_archives(self):
        """Without the dropped fix, the old formula over-archives."""
        # Same scenario as above but with the OLD formula (n_active as denominator):
        # avg = 700/10 = 70 → ceil(300/70) = 5 (over-archiving)
        # New formula: avg = 700/7 = 100 → ceil(300/100) = 3 (correct)
        cap, total_tok, events_tok, n_active, dropped = 1000, 900, 700, 10, 3
        target_tok = int(cap * _ARCHIVE_TARGET_UTILIZATION)
        tokens_to_shed = max(0, total_tok - target_tok)

        # Old formula
        old_avg = events_tok / max(1, n_active)
        old_n = min(int(math.ceil(tokens_to_shed / max(1, old_avg))), n_active)

        # New formula
        new_n = self._compute_n_to_archive(
            cap=cap, total_tok=total_tok, events_tok=events_tok, n_active=n_active, dropped=dropped
        )

        assert old_n > new_n  # Old over-archives
        assert new_n == 3
        assert old_n == 5

    def test_all_dropped_uses_denominator_one(self):
        """When all events are dropped, n_represented=max(1, 0)=1."""
        n = self._compute_n_to_archive(
            cap=1000, total_tok=800, events_tok=100, n_active=5, dropped=5
        )
        # avg = 100/1 = 100, tokens_to_shed = 200, ceil(200/100)=2
        assert n == 2

    def test_dropped_exceeds_n_active_clamped(self):
        """When dropped > n_active (shouldn't happen), max(1,...) prevents negative."""
        n = self._compute_n_to_archive(
            cap=1000, total_tok=800, events_tok=100, n_active=3, dropped=10
        )
        # n_represented = max(1, 3-10) = max(1, -7) = 1
        # avg = 100/1 = 100, tokens_to_shed = 200, ceil(200/100)=2
        assert n == 2


class TestArchiveTargetUtilization:
    """Verify the constant is what we expect."""

    def test_default_value(self):
        assert _ARCHIVE_TARGET_UTILIZATION == 0.60
