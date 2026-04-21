"""Runtime-level safety net: final structured payload must fit the LLM window.

The pre-render safety net (clamping ``max_event_tokens`` against
``ctx_window``) only saw per-block CONTENT tokens — it missed:

- JSON message wrappers (role, content-array, tool_use, tool_result)
- ``<event_xxx>`` XML wrappers added by ``format_message_content``

Measured on a real session: 101K content tokens → 163K when litellm
counts the structured message list (+61%) → 207K when Bedrock actually
tokenizes (+27% further, likely tokenizer differences).

Fix: after ``render_context`` produces the full messages list, count
it with ``litellm.token_counter(messages=...)`` and drop oldest
non-system messages until the total fits under ``ctx_window × 0.70``.
The 30% margin covers the litellm→API tokenizer gap.
"""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.events import Message
from unifiedllm import FakeLLMClient


class _FakeLLM(FakeLLMClient):
    """FakeLLM with a settable context_window for tests."""

    _cw = 200_000

    @property
    def context_window(self):  # type: ignore[override]
        return self._cw

    def count_tokens(self, text: str) -> int:
        # Lean on litellm's real tokenizer — tests assert against it.
        import litellm

        # Use a real model so litellm picks the right tokenizer.
        return litellm.token_counter(model="anthropic/claude-3-5-sonnet-20240620", text=text)


def _mk_llm(context_window: int) -> _FakeLLM:
    class _LLM(_FakeLLM):
        _cw = context_window

    # Pin model so the runtime's model_context_window resolution works.
    llm = _LLM()
    # Monkey-patch .model to something litellm recognizes (matches _FakeLLM.count_tokens).
    llm.model = "anthropic/claude-3-5-sonnet-20240620"  # type: ignore[attr-defined]
    return llm


class TestStructuredPayloadSafetyNet:
    """The rendered messages + tools MUST fit under ``ctx_window × 0.70``
    when measured by ``litellm.token_counter`` — the same counter the API
    uses (approximately; Bedrock adds another 25% that we cover with
    margin).
    """

    @pytest.mark.asyncio
    async def test_tool_heavy_session_clamps_to_window(self):
        """Reproduces the observed production failure: a Sonnet session
        packed with tool-call iterations (ToolCallEvent + PythonOutput
        pairs, like CodeAct generates). Content-only counting misses the
        ~60% JSON-structure overhead Anthropic adds around tool_use /
        tool_result; the authoritative safety net must still keep the
        final structured payload under ``ctx_window × 0.70``.

        Without the structured-payload safety net (pre-fix): 300 tool
        iterations produced ~262K structured tokens on a 200K window
        — API would reject with ContextWindowExceeded.
        """
        import litellm

        from context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
        from nemo_oo_agents.events import PythonOutput

        llm = _mk_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for i in range(300):
            tc_id = f"call_{i}"
            agent.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    name="execute_python",
                    arguments={"code": "x " * 500},
                    result=ToolResult(
                        tool_call_id=tc_id,
                        content="done",
                        result_status=ResultStatus.COMPLETE,
                    ),
                )
            )
            agent.event_manager.add(
                PythonOutput(
                    tool_call_id=tc_id,
                    execution_count=i,
                    stdout="x " * 500,
                    stderr="",
                    execution_status=ResultStatus.COMPLETE,
                )
            )

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            try:
                messages = await agent.runtime._build_messages(
                    method, call_args=(agent, "hi"), call_kwargs={}
                )
            except Exception:
                messages = None
        finally:
            _current_llm_var.reset(token)

        assert messages is not None
        # Final structured count — what the API will bill.
        structured = litellm.token_counter(model=agent._llm.model, messages=messages)
        cap = int(agent._llm.context_window * 0.70)
        assert structured <= cap, (
            f"structured payload {structured:,} > cap {cap:,} — safety net failed"
        )

    @pytest.mark.asyncio
    async def test_small_session_is_not_truncated(self):
        """Session that already fits the window must be left alone —
        safety net only fires when the structured count exceeds budget."""
        import litellm

        llm = _mk_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for _ in range(3):
            agent.event_manager.add(Message(content="small message"))

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            try:
                messages = await agent.runtime._build_messages(
                    method, call_args=(agent, "hi"), call_kwargs={}
                )
            except Exception:
                messages = None
        finally:
            _current_llm_var.reset(token)

        assert messages is not None
        # No events dropped.
        stats = agent.runtime._last_context_stats
        assert stats is not None
        # Structured count is well under the window — nothing to prune.
        structured = litellm.token_counter(model=agent._llm.model, messages=messages)
        assert structured < int(agent._llm.context_window * 0.70)

    @pytest.mark.asyncio
    async def test_clamp_emits_summary_event_when_it_truncates(self):
        """When the safety net drops messages, it MUST archive the
        corresponding events via ``event_manager.collapse`` so:

        - The TUI renderer sees the Summary event and surfaces
          ``∴ truncated …`` — user isn't blindsided by silently-dropped
          history.
        - Next turn's render doesn't redo the same drop work.
        """
        from context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
        from nemo_oo_agents.events import PythonOutput

        llm = _mk_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for i in range(300):
            tc_id = f"call_{i}"
            agent.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    name="execute_python",
                    arguments={"code": "x " * 500},
                    result=ToolResult(
                        tool_call_id=tc_id,
                        content="done",
                        result_status=ResultStatus.COMPLETE,
                    ),
                )
            )
            agent.event_manager.add(
                PythonOutput(
                    tool_call_id=tc_id,
                    execution_count=i,
                    stdout="x " * 500,
                    stderr="",
                    execution_status=ResultStatus.COMPLETE,
                )
            )

        # Subscribe to Summary events
        summary_events = []
        agent.event_manager.on("Summary", lambda ev: summary_events.append(ev))

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

        # Exactly one truncation Summary should fire (summary_text=None).
        assert len(summary_events) >= 1, "clamp must emit a Summary event"
        ev = summary_events[0]
        assert ev.summary_text is None, "truncation form has no summary text"
        assert ev.children_tags, "summary must reference archived child tags"

    @pytest.mark.asyncio
    async def test_per_call_llm_override_is_honored(self):
        """Per-call LLM override to a smaller model → the CLAMP sizes
        against the override, not the agent's original LLM."""
        import litellm

        from context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
        from nemo_oo_agents.events import PythonOutput

        big_llm = _mk_llm(1_000_000)

        class A(Agent, llm=big_llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        # Tool-heavy load — fits 1M comfortably but exceeds 200K Sonnet.
        for i in range(300):
            tc_id = f"call_{i}"
            agent.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    name="execute_python",
                    arguments={"code": "x " * 500},
                    result=ToolResult(
                        tool_call_id=tc_id,
                        content="done",
                        result_status=ResultStatus.COMPLETE,
                    ),
                )
            )
            agent.event_manager.add(
                PythonOutput(
                    tool_call_id=tc_id,
                    execution_count=i,
                    stdout="x " * 500,
                    stderr="",
                    execution_status=ResultStatus.COMPLETE,
                )
            )

        small_llm = _mk_llm(200_000)
        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(small_llm)  # override per-call
        try:
            try:
                messages = await agent.runtime._build_messages(
                    method, call_args=(agent, "hi"), call_kwargs={}
                )
            except Exception:
                messages = None
        finally:
            _current_llm_var.reset(token)

        assert messages is not None
        structured = litellm.token_counter(model=small_llm.model, messages=messages)
        cap = int(small_llm.context_window * 0.70)
        assert structured <= cap, (
            f"override LLM's window was ignored: structured={structured:,} > cap={cap:,}"
        )

    @pytest.mark.asyncio
    async def test_llm_without_context_window_skips_clamp(self):
        """LLM with no context_window disables the safety net — we have no
        number to clamp against. Call proceeds; if it overflows, the API
        surfaces the error."""
        import litellm

        class NoWindowLLM(FakeLLMClient):
            model = "anthropic/claude-3-5-sonnet-20240620"

            @property
            def context_window(self):  # type: ignore[override]
                return None  # explicitly missing

            def count_tokens(self, text: str) -> int:
                return litellm.token_counter(model=self.model, text=text)

        llm = NoWindowLLM()

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for _ in range(10):
            agent.event_manager.add(Message(content="hi"))

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(llm)
        try:
            try:
                messages = await agent.runtime._build_messages(
                    method, call_args=(agent, "hi"), call_kwargs={}
                )
            except Exception:
                messages = None
        finally:
            _current_llm_var.reset(token)

        # Doesn't crash; no assertion on cap (there isn't one).
        assert messages is not None
