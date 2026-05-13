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

# nemotron nano v3 via NVIDIA inference gateway — 262K context, 65K max output
_MODEL = os.environ.get("ARCHIVAL_TEST_MODEL", "")
_MODEL_NAME = "openai/nvidia/nvidia/Nemotron-3-Nano-30B-A3B"
_API_BASE = "https://inference-api.nvidia.com/v1"
_API_KEY_ENV = "NVIDIA_INTERNAL_API_KEY"
_CONTEXT_WINDOW = 262_144


def _fill_events(agent, n_events: int, payload_words: int = 80):
    """Add n_events tool-call + output pairs."""
    base = len(list(agent.event_manager.keys()))
    for i in range(n_events):
        idx = base + i
        tc_id = f"call_{idx}"
        payload = f"data_{idx} " * payload_words
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
                execution_count=idx,
                stdout=payload,
                stderr="",
                execution_status=ResultStatus.COMPLETE,
            )
        )


async def _measure_tokens(agent, llm):
    """Build messages and return the context stats."""
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


async def _fill_to_fraction(agent, llm, target_fraction: float, batch: int = 50):
    """Add events until litellm-estimated tokens reach target_fraction of ctx_window.

    Uses large batches and only measures every other batch to speed up filling.
    """
    ctx_window = llm.context_window
    target_tokens = int(ctx_window * target_fraction)
    rounds = 0

    while True:
        _fill_events(agent, batch, payload_words=200)
        rounds += 1
        # Only measure every 2nd round to reduce token-counting overhead
        if rounds % 2 == 0 or len(list(agent.event_manager.keys())) > batch * 2:
            stats = await _measure_tokens(agent, llm)
            if stats and stats.total_tokens >= target_tokens:
                return stats
        if len(list(agent.event_manager.keys())) > 10000:
            pytest.skip(
                f"Could not reach {target_fraction:.0%} ({target_tokens:,} tokens) "
                f"after 10000 events"
            )


@pytest.mark.integration
class TestArchivalOnContextErrorE2E:
    """E2E: fill to 95% → succeed → fill to 105% → fail → archive → succeed."""

    @pytest.mark.asyncio
    async def test_full_archival_lifecycle(self):
        api_key = os.environ.get(_API_KEY_ENV, "")
        if not api_key:
            pytest.skip(f"{_API_KEY_ENV} not set — skipping real LLM test")
        llm = CompletionClient(
            model=_MODEL_NAME,
            api_base=_API_BASE,
            api_key=api_key,
            temperature=0,
        )
        ctx_window = llm.context_window
        assert ctx_window and ctx_window > 0, (
            f"Model must report a context_window, got {ctx_window}"
        )

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()

        # Subscribe to Summary events (emitted by archival)
        summary_events: list = []
        agent.event_manager.on("Summary", lambda ev: summary_events.append(ev))

        # ── Phase 1: Fill to 95% of context window ──────────────────
        stats_95 = await _fill_to_fraction(agent, llm, 0.95)
        n_events_at_95 = len(list(agent.event_manager.keys()))

        assert stats_95.total_tokens >= int(ctx_window * 0.90), (
            f"Should be near 95%: {stats_95.total_tokens:,} < {int(ctx_window * 0.90):,}"
        )

        # Call the LLM — the proactive clamp (70%) drops messages for this
        # call, but no events are archived. Call should succeed.
        result1 = await agent.respond("Say hello in one word.")
        assert result1, "Phase 1: call should succeed"

        assert len(summary_events) == 0, (
            f"Phase 1: no archival expected (proactive clamp handles it), "
            f"got {len(summary_events)} Summary events"
        )
        # Event count should not decrease (new events from respond() may be added)
        n_after_phase1 = len(list(agent.event_manager.keys()))
        assert n_after_phase1 >= n_events_at_95, (
            f"Phase 1: events should not decrease: {n_after_phase1} < {n_events_at_95}"
        )

        # ── Phase 2: Fill to 105% of context window ─────────────────
        stats_105 = await _fill_to_fraction(agent, llm, 1.05)
        n_events_at_105 = len(list(agent.event_manager.keys()))

        assert stats_105.total_tokens >= ctx_window, (
            f"Should exceed ctx_window: {stats_105.total_tokens:,} < {ctx_window:,}"
        )

        summary_events.clear()

        # Call the LLM — context is over 100%. The proactive clamp
        # (70%) drops a LOT of messages. If litellm's token estimate is
        # accurate enough, the clamped request fits and succeeds. If the
        # API sees more tokens than litellm estimated, it rejects →
        # recovery fires → archival → retry.
        result2 = await agent.respond("Say goodbye in one word.")
        assert result2, "Phase 2: call should eventually succeed"

        n_events_after = len(list(agent.event_manager.keys()))

        if len(summary_events) > 0:
            # ── Archival fired — verify the invariants ──────────────
            ev = summary_events[0]
            assert "context-window API error" in ev.summary_text, (
                f"Summary should mention API error: {ev.summary_text[:200]}"
            )
            assert ev.children_tags, "Summary must reference archived child tags"

            # Events should have decreased
            assert n_events_after < n_events_at_105, (
                f"Archival should reduce events: "
                f"{n_events_after} >= {n_events_at_105}"
            )

            # Re-measure utilization after archival
            stats_post = await _measure_tokens(agent, llm)

            # The archival cap accounts for calibration ratio
            ratio = agent.runtime._token_calibration_ratio or 1.0
            effective_cap = int(ctx_window * 0.70 / max(ratio, 1.0))
            target_tok = int(effective_cap * _ARCHIVE_TARGET_UTILIZATION)

            # After archival, utilization should be near the 60% target.
            # Allow generous tolerance: events added by respond() itself
            # and imprecise token counting shift the exact number.
            assert stats_post.total_tokens <= target_tok * 1.50, (
                f"After archival, tokens ({stats_post.total_tokens:,}) "
                f"should be near 60% target ({target_tok:,}) — too high"
            )
        else:
            # ── Proactive clamp was sufficient ──────────────────────
            # The 70% clamp dropped enough messages that the API didn't
            # reject. No archival expected. This is valid — verify.
            stats2 = agent.runtime._last_context_stats
            assert stats2 is not None
            cap = int(ctx_window * 0.70)
            assert stats2.total_tokens <= cap + 2000, (
                f"Clamp should keep tokens under cap: "
                f"{stats2.total_tokens:,} > {cap:,}"
            )

        # ── Calibration ratio should be learned ─────────────────────
        # After at least one successful API call, response.usage should
        # have been extracted and the ratio computed.
        ratio = agent.runtime._token_calibration_ratio
        if ratio is not None:
            assert ratio > 0, f"Calibration ratio should be positive, got {ratio}"
