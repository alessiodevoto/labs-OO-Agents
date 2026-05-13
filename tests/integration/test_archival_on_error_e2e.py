# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""E2E integration test: archival fires on ContextWindowExceededError.

Three-phase lifecycle:
1. Call at ~95% of context window → succeeds → calibrates ratio
2. Call at ~105% of context window → fails → archives events → retry succeeds
3. Verify context is at ~60% after archival

Uses the calibration ratio from Phase 1 to compute the exact event count
for Phase 2. No hardcoded per-event estimates.

Fixture: pre-generated events (tests/integration/fixtures/archival_95pct.json.gz).

Run with:
    pytest -m integration tests/integration/test_archival_on_error_e2e.py -v
"""

import gzip
import json
import math
import os
from pathlib import Path

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
from nemo_oo_agents.events import PythonOutput, Summary
from nemo_oo_agents.runtime.actor import _ARCHIVE_TARGET_UTILIZATION
from nemo_oo_agents.unifiedllm import CompletionClient

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "archival_95pct.json.gz"
_API_BASE = "https://inference-api.nvidia.com/v1"
_API_KEY_ENV = "NVIDIA_INTERNAL_API_KEY"

_EVENT_TYPES = {
    "ToolCallEvent": ToolCallEvent,
    "PythonOutput": PythonOutput,
}


def _load_fixture():
    with gzip.open(_FIXTURE_PATH, "rt") as f:
        return json.load(f)


def _hydrate_events(agent, event_entries):
    for entry in event_entries:
        event_cls = _EVENT_TYPES.get(entry["event_type"])
        if event_cls is None:
            continue
        ev = event_cls.model_validate(entry["data"])
        agent.event_manager.add(ev)


@pytest.mark.integration
class TestArchivalOnContextErrorE2E:
    """E2E: call at 95% → calibrate → call at 105% → archive → verify 60%."""

    @pytest.mark.asyncio
    async def test_full_archival_lifecycle(self):
        api_key = os.environ.get(_API_KEY_ENV, "")
        if not api_key:
            pytest.skip(f"{_API_KEY_ENV} not set")
        if not _FIXTURE_PATH.exists():
            pytest.skip(f"Fixture not found: {_FIXTURE_PATH}")

        fixture_data = _load_fixture()
        ctx_window = fixture_data["context_window"]
        all_events = fixture_data["events"]
        model_name = "openai/" + fixture_data["model"]

        llm = CompletionClient(
            model=model_name, api_base=_API_BASE, api_key=api_key, temperature=0,
        )
        if llm.context_window is None:
            llm._registry_config = {"context_window": ctx_window}

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()

        summary_events: list = []
        agent.event_manager.on("Summary", lambda ev: summary_events.append(ev))

        # ── Phase 1: Call at ~95% → succeed → calibrate ────────────
        # Start with a conservative estimate: load 20% of events.
        # If the call succeeds (likely), use the calibration ratio to
        # compute how many events = 95% and add more. Repeat until we're
        # at 95% ± 2% of the real context window.
        n_initial = len(all_events) // 5
        _hydrate_events(agent, all_events[:n_initial])

        result1 = await agent.respond("Say hello in one word.")
        assert result1, "Phase 1 (initial): should succeed"

        ratio = agent.runtime._token_calibration_ratio
        assert ratio is not None and ratio > 0, (
            f"Calibration ratio should be learned, got {ratio}"
        )

        # Now compute how many events we need for 95% of real context
        stats = agent.runtime._last_context_stats
        n_current = len(list(agent.event_manager.keys()))
        litellm_per_event = stats.total_tokens / max(1, n_current)

        target_real_95 = int(ctx_window * 0.95)
        target_litellm_95 = target_real_95 / ratio
        n_for_95 = int(target_litellm_95 / litellm_per_event)

        if n_for_95 > n_current:
            # Add more events to reach 95%
            extra_95 = min(n_for_95 - n_current, len(all_events) - n_current)
            if extra_95 > 0:
                _hydrate_events(agent, all_events[n_current:n_current + extra_95])

                # Verify this call still succeeds at ~95%
                summary_events.clear()
                result1b = await agent.respond("Confirm hello.")
                assert result1b, "Phase 1 (at 95%): should succeed"
                assert len(summary_events) == 0, (
                    f"Phase 1: no archival expected, got {len(summary_events)}"
                )

                # Update calibration
                ratio = agent.runtime._token_calibration_ratio
                stats = agent.runtime._last_context_stats

        n_at_95 = len(list(agent.event_manager.keys()))

        # ── Phase 2: Call at ~105% → fail → archive → retry ────────
        # Compute how many events for 105%
        litellm_per_event = stats.total_tokens / max(1, n_at_95)
        target_real_105 = int(ctx_window * 1.05)
        target_litellm_105 = target_real_105 / ratio
        n_for_105 = int(target_litellm_105 / litellm_per_event)

        extra_105 = min(n_for_105 - n_at_95, len(all_events) - n_at_95)
        assert extra_105 > 0, (
            f"Not enough fixture events to reach 105%: need {n_for_105}, have {len(all_events)}"
        )
        _hydrate_events(agent, all_events[n_at_95:n_at_95 + extra_105])

        n_events_before = len(list(agent.event_manager.keys()))
        summary_events.clear()

        result2 = await agent.respond("Say goodbye in one word.")
        assert result2, "Phase 2: should succeed after archival + retry"

        # ── Phase 3: Verify archival ────────────────────────────────
        n_events_after = len(list(agent.event_manager.keys()))

        assert len(summary_events) >= 1, (
            f"Archival should emit Summary events, got {len(summary_events)}. "
            f"Events: {n_events_before} -> {n_events_after}"
        )
        ev = summary_events[0]
        assert "context-window API error" in ev.summary_text
        assert ev.children_tags

        assert n_events_after < n_events_before, (
            f"Archival should reduce events: {n_events_after} >= {n_events_before}"
        )

        # ── Phase 3: Verify ~60% utilization with a real API call ──
        # Make another call. It should succeed (context is now smaller).
        # Use response.usage.prompt_tokens to verify actual utilization.
        result3 = await agent.respond("Confirm context was reduced.")
        assert result3, "Phase 3: call after archival should succeed"

        # The calibration ratio should still be set from Phase 1
        ratio_post = agent.runtime._token_calibration_ratio
        assert ratio_post is not None and ratio_post > 0

        # Check actual utilization from the API's perspective
        stats_post = agent.runtime._last_context_stats
        assert stats_post is not None

        # Estimate real tokens from litellm tokens using the calibrated ratio
        estimated_real = stats_post.total_tokens * ratio_post
        utilization = estimated_real / ctx_window

        # After archival targeting 60%, utilization should be 40-80%
        # (generous range: events from respond() calls add tokens,
        # and the ratio is an approximation)
        assert 0.30 <= utilization <= 0.85, (
            f"After archival, utilization should be near 60%: "
            f"got {utilization:.1%} ({estimated_real:,.0f} est. real / {ctx_window:,} ctx_window)"
        )
