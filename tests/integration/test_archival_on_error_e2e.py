# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""E2E integration test: archival fires on ContextWindowExceededError.

Three-phase lifecycle:
1. Call at ~95% of context window → succeeds → calibrates ratio
2. Call at ~105% of context window → fails → archives events
3. Verify context is at ~60% after archival

Fixture: pre-generated events (tests/integration/fixtures/archival_95pct.json.gz).

Run with:
    pytest -m integration tests/integration/test_archival_on_error_e2e.py -v
"""

import gzip
import json
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

# From empirical measurement (see issue #204):
# 800 fixture events = 742K litellm tokens = 1,203K real API tokens.
# Per event: ~928 litellm tokens, ~1,504 real tokens.
# These constants are used to estimate how many events to load for each phase.
_REAL_TOKENS_PER_EVENT = 1_504
_LITELLM_TOKENS_PER_EVENT = 928

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


def _events_for_real_fraction(ctx_window: int, fraction: float, total_events: int) -> int:
    """Estimate how many fixture events to load for a given fraction of the real context."""
    target_real_tokens = int(ctx_window * fraction)
    n = int(target_real_tokens / _REAL_TOKENS_PER_EVENT)
    return min(n, total_events)


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

        # ── Phase 1: Call at ~95% of real context ───────────────────
        n_phase1 = _events_for_real_fraction(ctx_window, 0.95, len(all_events))
        _hydrate_events(agent, all_events[:n_phase1])

        result1 = await agent.respond("Say hello in one word.")
        assert result1, "Phase 1: call at ~95% should succeed"
        assert len(summary_events) == 0, (
            f"Phase 1: no archival expected, got {len(summary_events)}"
        )

        # Calibration ratio should be learned from the successful call
        ratio = agent.runtime._token_calibration_ratio
        assert ratio is not None, (
            "Calibration ratio should be learned from response.usage"
        )
        assert ratio > 1.0, (
            f"Expected ratio > 1.0 (litellm undercounts), got {ratio:.2f}"
        )

        # ── Phase 2: Call at ~105% of real context ──────────────────
        n_phase2 = _events_for_real_fraction(ctx_window, 1.05, len(all_events))
        extra_events = all_events[n_phase1:n_phase2]
        _hydrate_events(agent, extra_events)

        n_events_before = len(list(agent.event_manager.keys()))
        summary_events.clear()

        result2 = await agent.respond("Say goodbye in one word.")
        assert result2, "Phase 2: call should succeed after archival + retry"

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

        # Verify utilization is near 60% target
        # After archival: events should represent ~60% of the effective cap
        effective_cap = int(ctx_window * 0.70 / max(ratio, 1.0))
        target_tok = int(effective_cap * _ARCHIVE_TARGET_UTILIZATION)
        estimated_remaining_litellm = n_events_after * _LITELLM_TOKENS_PER_EVENT
        # Allow generous tolerance — these are estimates
        assert estimated_remaining_litellm <= target_tok * 1.5, (
            f"After archival, estimated {estimated_remaining_litellm:,} litellm tokens "
            f"should be near target {target_tok:,} (60% of cap)"
        )
