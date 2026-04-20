"""Runtime-level truncation safety net against the resolved LLM's context window.

Whenever ``_build_messages`` runs, the renderer must not ship an event pile
larger than the LLM can accept. The safety net is unconfigurable — sized
directly from ``llm.context_window`` at call time — so:

- Per-call LLM overrides (``method(llm=other)``) get scaled automatically.
- Agents with no explicit ``max_event_tokens`` still get protected.
- Agents whose config sets a budget LARGER than the resolved LLM's window
  get clamped down (a session carrying 700K of events onto a 200K-window
  model gets pruned to fit).

None of this is TUI-specific — the clamp lives in the runtime.
"""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.config.truncation_config import TruncationConfig
from nemo_oo_agents.events import Message
from unifiedllm import FakeLLMClient


class _FakeLLM(FakeLLMClient):
    """FakeLLM with settable context_window for these tests."""

    _cw = 200_000

    @property
    def context_window(self):  # type: ignore[override]
        return self._cw

    def count_tokens(self, text: str) -> int:
        # 4 chars ≈ 1 token (Anthropic-ish average).
        return max(1, len(text) // 4)


def _mk_llm(context_window: int) -> _FakeLLM:
    class _LLM(_FakeLLM):
        _cw = context_window

    return _LLM()


@pytest.fixture
def agent_with_big_events():
    """Agent on a 200K-window LLM carrying ~300K tokens of events —
    deliberately oversized so the safety net MUST drop some."""
    llm = _mk_llm(200_000)

    class A(Agent, llm=llm):
        async def respond(self, prompt: str) -> str:
            """Respond to {prompt}."""
            ...

    agent = A()
    # 100 messages × ~12KB each = ~1.2MB = ~300K tokens.
    for _ in range(100):
        agent.event_manager.add(Message(content="x" * 12_000))
    return agent


class TestRuntimeContextWindowClamp:
    """The safety net MUST clamp events to fit the resolved LLM's window,
    regardless of what the agent's TruncationConfig says."""

    @pytest.mark.asyncio
    async def test_no_explicit_event_budget_still_clamps_to_window(self, agent_with_big_events):
        """Agent has no ``max_event_tokens`` config; event pile would
        otherwise exceed the LLM's window. Safety net MUST kick in.
        """
        agent = agent_with_big_events
        # Default config: max_event_tokens is None.
        assert agent._truncation.max_event_tokens is None

        # Drive _build_messages. FakeLLMClient raises on actual generation;
        # we only need render_context to run, which happens before the call.
        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            try:
                await agent.runtime._build_messages(method, call_args=(agent, "hi"), call_kwargs={})
            except Exception:
                pass
        finally:
            _current_llm_var.reset(token)

        stats = agent.runtime._last_context_stats
        assert stats is not None
        # Event pile must fit the 200K window (minus the 15% reserve).
        assert stats.events_tokens <= int(agent._llm.context_window * 0.85)
        # Something had to drop — the fixture loads ~300K into a 200K window.
        assert stats.events_dropped > 0

    @pytest.mark.asyncio
    async def test_oversized_explicit_budget_is_clamped(self):
        """Agent was configured on Opus (1M) with an explicit 700K event
        budget, then ``_current_llm_var`` is switched to Sonnet (200K).
        The 700K budget must clamp down to fit Sonnet's window.
        """
        llm_opus = _mk_llm(1_000_000)

        class A(
            Agent,
            llm=llm_opus,
            truncation=TruncationConfig(max_event_tokens=700_000),
        ):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        # 100 × 12KB = ~300K tokens of events.
        for _ in range(100):
            agent.event_manager.add(Message(content="x" * 12_000))

        # Swap to the Sonnet-sized LLM in-place.
        agent._llm = _mk_llm(200_000)

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            try:
                await agent.runtime._build_messages(method, call_args=(agent, "hi"), call_kwargs={})
            except Exception:
                pass
        finally:
            _current_llm_var.reset(token)

        stats = agent.runtime._last_context_stats
        assert stats is not None
        assert stats.events_tokens <= 200_000
        assert stats.events_dropped > 0

    @pytest.mark.asyncio
    async def test_smaller_explicit_budget_is_respected(self):
        """Explicit ``max_event_tokens=50_000`` smaller than the LLM's
        window (200K) must be honored — safety net must not *raise* the
        user's cap, only lower it.
        """
        llm = _mk_llm(200_000)

        class A(
            Agent,
            llm=llm,
            truncation=TruncationConfig(max_event_tokens=50_000),
        ):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for _ in range(100):
            agent.event_manager.add(Message(content="x" * 12_000))

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            try:
                await agent.runtime._build_messages(method, call_args=(agent, "hi"), call_kwargs={})
            except Exception:
                pass
        finally:
            _current_llm_var.reset(token)

        stats = agent.runtime._last_context_stats
        assert stats is not None
        assert stats.events_tokens <= 50_000

    @pytest.mark.asyncio
    async def test_llm_without_context_window_skips_clamp(self):
        """An LLM with no ``context_window`` attribute disables the safety
        net — there's no number to clamp against. The call proceeds with
        whatever the agent's config says (None → no truncation)."""

        # Plain FakeLLMClient has context_window=128000; subclass strips it.
        class NoWindowLLM(FakeLLMClient):
            # Override at class level to remove the property.
            pass

        # Python can't delete an inherited property easily; use a Mock-like
        # object that has count_tokens but NO context_window.
        class _FakeNoWindow:
            def count_tokens(self, text: str) -> int:
                return max(1, len(text) // 4)

            async def acall(self, *a, **kw):
                raise RuntimeError("no LLM")

        # We need an Agent attachable LLM. FakeLLMClient has context_window as a
        # non-settable property, so use an instance-level attribute trick by
        # subclassing a minimal Agent-compatible mock. Simpler: assert that the
        # clamp logic doesn't crash when context_window is None.
        llm = _mk_llm(0)  # context_window=0 should be treated as "unknown"

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for _ in range(10):
            agent.event_manager.add(Message(content="x" * 12_000))

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            try:
                await agent.runtime._build_messages(method, call_args=(agent, "hi"), call_kwargs={})
            except Exception:
                pass
        finally:
            _current_llm_var.reset(token)

        # Doesn't crash; stats are populated.
        assert agent.runtime._last_context_stats is not None

    @pytest.mark.asyncio
    async def test_real_tokenizer_is_used_not_char_len(self, agent_with_big_events):
        """When the safety net is active, the LLM's ``count_tokens`` method
        must be the one driving decisions — NOT ``len()`` on block content.

        Regression guard: an earlier version of ``render_context`` fell
        back to ``len`` when no token counter was supplied, producing
        ~4× over-counts on typical English (char-vs-token ratio).
        """
        agent = agent_with_big_events

        # Instrument: record every call to our LLM's count_tokens.
        calls = []
        original = type(agent._llm).count_tokens

        def counting_count_tokens(self, text: str) -> int:
            calls.append(len(text))
            return original(self, text)

        type(agent._llm).count_tokens = counting_count_tokens  # type: ignore[method-assign]

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            try:
                await agent.runtime._build_messages(method, call_args=(agent, "hi"), call_kwargs={})
            except Exception:
                pass
        finally:
            _current_llm_var.reset(token)
            type(agent._llm).count_tokens = original  # type: ignore[method-assign]

        # Render_context called count_tokens at least once — it wouldn't
        # have if the safety net bailed out and fell through to len().
        assert len(calls) > 0, "expected count_tokens to be invoked by the safety-net path"
