"""Integration test: verify TUIAgent can be instantiated with all new methods."""

from unittest.mock import MagicMock


def test_tui_agent_instantiation():
    """TUIAgent should instantiate with all workflow methods available."""
    from nemo_oo_agents_cli.tui.agent import TUIAgent

    # Check all expected methods exist
    expected_methods = [
        "respond",
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


def test_respond_is_per_turn_with_notification_and_restored():
    """``respond()`` is invoked per turn by the outer dispatcher, taking
    a ``(queue_name, item)`` notification and an optional ``restored``
    dict. Replaces the older orchestrator-era signature check."""
    import inspect

    from nemo_oo_agents_cli.tui.agent import BaseTUIAgent

    sig = inspect.signature(BaseTUIAgent.respond)
    assert list(sig.parameters.keys()) == ["self", "notification", "restored"]
    assert sig.parameters["restored"].default is None
