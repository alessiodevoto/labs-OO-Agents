"""Regression tests for issue 180.

`TokenBudgetSummarizer.summarize()` must accept arbitrarily large input —
its purpose is to compress oversized history. Before this fix the default
PredictConfig.max_param_chars=200_000 + TruncationConfig defaults rejected
anything past those bounds, causing the summarizer to silently fail with
a WARNING and leaving history uncompressed (catch-22).

These tests pin the per-method @strategy / truncation overrides on
SummarizationAgent.summarize so future refactors don't quietly reintroduce
the bug.
"""

from nemo_oo_agents.agents import MethodSummarizer, SummarizationAgent
from nemo_oo_agents.config.strategy_config import PredictConfig
from nemo_oo_agents.strategies.current_call import CurrentCall
from nemo_oo_agents.strategies.predict import PredictStrategy


def _assert_format_unconstrained(fc) -> None:
    """All three structural bounds must be ``None`` (unconstrained)."""
    assert fc.max_string is None
    assert fc.max_length is None
    assert fc.max_depth is None


def test_summarize_strategy_override_present():
    """The @strategy decorator stores _strategy_override on the inner func.

    The wrapper preserves access via functools.wraps' __wrapped__.
    ``None`` means the parameter-size guard is disabled — the summarizer's
    contract is "accept arbitrarily large input."
    """
    inner = SummarizationAgent.summarize.__wrapped__
    override = inner._strategy_override
    assert isinstance(override, PredictStrategy)
    assert override.config.max_param_chars is None


def test_summarize_truncation_override_present():
    """_strategy_truncation is set on the wrapper directly (decorators.py:128).

    All four guards must be lifted: max_block_chars, plus every structural
    bound on prefill_format and event_format. ``None`` is the framework's
    "unconstrained" sentinel (matching pformat's max_string=None semantics).
    """
    tc = SummarizationAgent.summarize._strategy_truncation
    assert tc is not None
    assert tc.max_block_chars is None
    _assert_format_unconstrained(tc.prefill_format)
    _assert_format_unconstrained(tc.event_format)


def test_method_summarizer_inherits_overrides():
    """MethodSummarizer inherits summarize() from SummarizationAgent.

    Same identity-level test on both attribute paths to lock the inheritance
    contract (the override is on the base class, not redefined per subclass).
    """
    inner = MethodSummarizer.summarize.__wrapped__
    assert inner._strategy_override.config.max_param_chars is None

    tc = MethodSummarizer.summarize._strategy_truncation
    assert tc is not None
    assert tc.max_block_chars is None
    _assert_format_unconstrained(tc.prefill_format)
    _assert_format_unconstrained(tc.event_format)


def _summarizer_call(history_markdown: str) -> CurrentCall:
    """Build a CurrentCall mirroring SummarizationAgent.summarize's signature."""
    return CurrentCall(
        id="test-id",
        method_name="summarize",
        decorator="agent",
        signature="(self, history_markdown: str, target_chars: int)",
        args=(history_markdown, 1000),
        kwargs={},
    )


def test_assert_param_sizes_noop_when_limit_is_none():
    """``max_param_chars=None`` disables the guard entirely.

    The reproduction from issue 180: a 1 M-char history must not be rejected
    when the summarizer's PredictConfig sets ``max_param_chars=None``. With
    the default 200 K limit this raised ValueError and caused the summarizer
    to silently abort.
    """
    strategy = PredictStrategy(PredictConfig(max_param_chars=None))
    call = _summarizer_call("x" * 1_000_000)
    strategy._assert_param_sizes(call)  # must not raise


def test_assert_param_sizes_still_fires_when_limit_is_set():
    """Non-summarizer callers keep the safety guard.

    The decorator-scoped ``None`` for the summarizer must NOT change the
    behavior of ordinary PredictStrategy callers — the global default is
    still 200 K.
    """
    import pytest

    strategy = PredictStrategy(PredictConfig(max_param_chars=1000))
    call = _summarizer_call("x" * 5_000)
    with pytest.raises(ValueError, match="exceeding max_param_chars"):
        strategy._assert_param_sizes(call)
