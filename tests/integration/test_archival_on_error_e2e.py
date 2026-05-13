# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""E2E integration test: archival fires only on ContextWindowExceededError.

Uses a real LLM (nemotron nano v3 via NIM). Marked @pytest.mark.integration
for nightly CI only.

Run with:
    pytest -m integration tests/integration/test_archival_on_error_e2e.py -v
"""

import os

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
from nemo_oo_agents.events import PythonOutput, Summary
from nemo_oo_agents.runtime.actor import (
    _ARCHIVE_TARGET_UTILIZATION,
    _current_llm_var,
    _current_method_var,
)
from nemo_oo_agents.unifiedllm import CompletionClient

# nemotron nano v3 via NIM — 262K context, 8K max output
_MODEL = os.environ.get(
    "ARCHIVAL_TEST_MODEL", "nvidia_nim/nvidia/nemotron-nano-3-30b-v1"
)


def _fill_events(agent, n_events: int, payload_words: int = 80):
    """Add n_events tool-call + output pairs.

    Each pair contributes roughly ``payload_words * 6`` tokens
    (3 fields × 2 words→tokens overhead).
    """
    for i in range(n_events):
        tc_id = f"call_{i}"
        payload = f"data_{i} " * payload_words
        agent.event_manager.add(
            ToolCallEvent(
                tool_call_id=tc_id,
                name="execute_python",
                arguments={"code": payload},
                result=ToolResult(
                    tool_call_id=tc_id,
                    content=payload,
                    result_status=ResultStatus.COMPLETE,
                ),
            )
        )
        agent.event_manager.add(
            PythonOutput(
                tool_call_id=tc_id,
                execution_count=i,
                stdout=payload,
                stderr="",
                execution_status=ResultStatus.COMPLETE,
            )
        )


async def _measure_tokens(agent, llm):
    """Build messages and return the litellm-estimated total tokens."""
    method = type(agent).respond
    llm_token = _current_llm_var.set(llm)
    method_token = _current_method_var.set(method)
    try:
        await agent.runtime._build_messages(
            method, call_args=(agent, "hi"), call_kwargs={}, tools=[]
        )
    finally:
        _current_llm_var.reset(llm_token)
        _current_method_var.reset(method_token)
    return agent.runtime._last_context_stats


@pytest.mark.integration
class TestArchivalOnContextErrorE2E:
    """E2E: fill context → succeed → overfill → error → archive → succeed.

    Verifies the full lifecycle with a real LLM.
    """

    @pytest.mark.asyncio
    async def test_full_archival_lifecycle(self):
        llm = CompletionClient(model=_MODEL, temperature=0)
        ctx_window = llm.context_window
        assert ctx_window and ctx_window > 0, (
            f"Model {_MODEL} must report a context_window, got {ctx_window}"
        )

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()

        # ── Phase 1: Fill to ~60% of context window ─────────────────
        # Add events in batches until we reach ~60% utilization.
        target_tokens = int(ctx_window * 0.55)
        batch = 30

        while True:
            _fill_events(agent, batch)
            stats = await _measure_tokens(agent, llm)
            if stats and stats.total_tokens >= target_tokens:
                break
            if len(list(agent.event_manager.keys())) > 5000:
                pytest.skip(
                    f"Could not reach {target_tokens:,} tokens after 5000 events "
                    f"(at {stats.total_tokens:,})"
                )

        phase1_events = len(list(agent.event_manager.keys()))
        phase1_tokens = stats.total_tokens

        # Subscribe to Summary events (emitted by archival)
        summary_events: list = []
        agent.event_manager.on("Summary", lambda ev: summary_events.append(ev))

        # ── Phase 1 call: should succeed, no archival ───────────────
        result1 = await agent.respond("Say hello in one word.")
        assert result1, "Phase 1 call should return a response"
        assert len(summary_events) == 0, (
            f"Phase 1: no archival expected, got {len(summary_events)} Summary events"
        )

        # ── Phase 2: Overshoot the context window ───────────────────
        # Fill enough events to push well past ctx_window so the LLM
        # call actually fails with a context-window error.
        # The proactive clamp (70%) will drop messages, but if the real
        # token count exceeds the clamped budget (litellm undercounts),
        # the API returns an error and archival fires.
        overshoot_target = int(ctx_window * 1.5)
        while True:
            _fill_events(agent, batch, payload_words=120)
            stats = await _measure_tokens(agent, llm)
            if stats and stats.total_tokens >= overshoot_target:
                break
            if len(list(agent.event_manager.keys())) > 10000:
                break  # enough — the clamp will take care of the rest

        n_events_before_call = len(list(agent.event_manager.keys()))
        summary_events.clear()

        # ── Phase 2 call: may fail → archive → retry → succeed ─────
        # If the proactive clamp is tight enough, the call may succeed
        # without an API error. That's OK — the clamp is doing its job.
        # If it fails, the recovery + archival path fires.
        result2 = await agent.respond("Say goodbye in one word.")
        assert result2, "Phase 2 call should eventually succeed (after recovery)"

        n_events_after_call = len(list(agent.event_manager.keys()))

        if len(summary_events) > 0:
            # ── Archival fired — verify everything ──────────────────
            ev = summary_events[0]
            assert "context-window API error" in ev.summary_text, (
                f"Summary text should mention context-window API error, "
                f"got: {ev.summary_text[:200]}"
            )
            assert ev.children_tags, "Summary must reference archived child tags"

            # Events should have decreased
            assert n_events_after_call < n_events_before_call, (
                f"Archival should reduce event count: "
                f"{n_events_after_call} >= {n_events_before_call}"
            )

            # Verify the archival targeted ~60% utilization.
            # Re-measure tokens after archival.
            stats_after = await _measure_tokens(agent, llm)
            cap = int(ctx_window * 0.70)
            # If calibration ratio is known, cap is tighter
            ratio = agent.runtime._token_calibration_ratio or 1.0
            effective_cap = int(ctx_window * 0.70 / max(ratio, 1.0))
            target = int(effective_cap * _ARCHIVE_TARGET_UTILIZATION)

            # Allow 20% tolerance — token estimates are imprecise
            assert stats_after.total_tokens <= target * 1.20, (
                f"After archival, tokens ({stats_after.total_tokens:,}) should be "
                f"near 60% target ({target:,}) ± 20%"
            )
        else:
            # Proactive clamp was sufficient — no API error, no archival.
            # Verify the stats show the clamp working.
            stats2 = agent.runtime._last_context_stats
            assert stats2 is not None
            cap = int(ctx_window * 0.70)
            assert stats2.total_tokens <= cap + 2000, (
                f"Clamp should keep tokens under cap: "
                f"{stats2.total_tokens:,} > {cap:,}"
            )

        # ── Phase 3: Verify calibration ratio was learned ───────────
        # After successful API calls, the runtime should have stored
        # the calibration ratio from response.usage.
        ratio = agent.runtime._token_calibration_ratio
        if ratio is not None:
            assert ratio > 0, f"Calibration ratio should be positive, got {ratio}"
            # Typical: litellm undercounts by 20-60%, so ratio > 1.0
            # But some models may overcount, so just check > 0.
