# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration test: verify TUIAgent can be instantiated with all new methods."""

from unittest.mock import MagicMock


def test_tui_agent_instantiation():
    """TUIAgent should instantiate with all workflow methods available."""
    from nemo_oo_agents_cli.tui.agent import TUIAgent

    # Check all expected methods exist
    expected_methods = [
        "handle",
        "classify_intent",
        "answer_question",
        "_legacy_brainstorm",
        "write_plan",
        "implement_step",
        "debug_issue",
        "verify_work",
        "review_changes",
    ]
    for name in expected_methods:
        assert hasattr(TUIAgent, name), f"Missing method: {name}"


def test_tui_agent_phase_tracking_initial_state():
    """New TUIAgent instance should start in idle phase."""
    from nemo_oo_agents_cli.tui.agent import TUIAgent

    mock_llm = MagicMock()
    agent = TUIAgent(llm=mock_llm)

    assert agent._phase == "idle"
    assert agent._workflow_state == {}


def test_handle_is_per_turn_with_notification():
    """``handle()`` is invoked per turn by the outer dispatcher, taking
    a ``(queue_name, item)`` notification. Replaces the older
    orchestrator-era signature check."""
    import inspect

    from nemo_oo_agents_cli.tui.agent import BaseTUIAgent

    sig = inspect.signature(BaseTUIAgent.handle)
    assert list(sig.parameters.keys()) == ["self", "notification"]
    # notification is dict[str, list]
