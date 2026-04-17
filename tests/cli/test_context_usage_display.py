# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for context-window usage shown in the TUI.

Two surfaces:

1. The status-bar rule between turns includes a compact ``"ctx N%"``
   label whenever the agent has a bounded context and the runtime has
   rendered at least one generation.
2. The TUI agent registers a dynamic ``context_usage`` block in its
   ``context_manager`` so the LLM sees the same stats every turn.
"""

from types import SimpleNamespace

# ── _context_usage_label -----------------------------------------------------


def _make_session_for_label(context_stats):
    """Build a minimal Session whose ``_context_usage_label`` we can call."""
    from nemo_oo_agents_cli.tui.session import Session

    session = Session.__new__(Session)
    session.agent = SimpleNamespace(context_stats=context_stats)
    return session


def _stats(*, total, max_context, max_event):
    """ContextWindowStats-shaped namespace — enough for the label helper."""
    return SimpleNamespace(
        total_tokens=total,
        max_context_tokens=max_context,
        max_event_tokens=max_event,
    )


def test_context_usage_label_none_when_no_stats():
    session = _make_session_for_label(None)
    assert session._context_usage_label() == ""


def test_context_usage_label_none_when_no_max():
    # Stats exist but both limits are None → no percent to show
    session = _make_session_for_label(_stats(total=1234, max_context=None, max_event=None))
    assert session._context_usage_label() == ""


def test_context_usage_label_shows_integer_percent():
    # 16 000 used / (64 000 + 16 000) = 20.0%
    session = _make_session_for_label(_stats(total=16_000, max_context=64_000, max_event=16_000))
    assert session._context_usage_label() == "ctx 20%"


def test_context_usage_label_rounds():
    # 13 000 / 80 000 = 16.25% → "ctx 16%"
    session = _make_session_for_label(_stats(total=13_000, max_context=64_000, max_event=16_000))
    assert session._context_usage_label() == "ctx 16%"


# ── TUI agent registers the context_usage dynamic block -----------------------


def test_tui_agent_installs_context_usage_dynamic_block():
    """BaseTUIAgent.__init__ registers a dynamic context_usage block.

    The expression depends only on ``self.context_stats`` so it stays
    None-safe on the very first turn (when no generation has run).
    """
    import os

    # Don't actually hit any LLM — pin to a FakeLLMClient and a
    # minimum-viable config.
    from pathlib import Path

    from nemo_oo_agents_cli.tui.agent import TUIAgent
    from nemo_oo_agents_cli.tui.config import AgentConfig, SummarizationConfig
    from unifiedllm import FakeLLMClient

    agent_cfg = AgentConfig(
        working_dir=Path(os.getcwd()),
        summarization=SummarizationConfig(policy="none"),
    )
    agent = TUIAgent(llm=FakeLLMClient(), config=agent_cfg)

    # Dynamic context keys are stored on the context manager
    cm = agent.context_manager
    keys = list(cm.keys())
    assert "context_usage" in keys, f"context_usage missing; have: {keys}"
