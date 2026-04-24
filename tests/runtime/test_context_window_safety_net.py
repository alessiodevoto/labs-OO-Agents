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


class TestTokenCounterRegression:
    """Regression tests for the two bugs root-caused in issue #133."""

    @pytest.mark.asyncio
    async def test_stats_context_blocks_tokens_is_tokens_not_chars(self):
        """Bug #1: when no truncation limits are set, ``render_context`` used
        to fall back to ``len``, so ``ContextWindowStats.{context_blocks,
        events,total}_tokens`` were character counts, not token counts. For
        English prose this over-reports by ~4×, breaking any consumer of the
        stats (TUI "ctx N%", observability, etc.).

        After the fix, ``_build_messages`` always passes a real counter (the
        LLM's ``count_tokens`` or ``char_approximate_token_counter`` as
        fallback). We assert against ``context_blocks_tokens`` because it's
        the field the structured-payload safety net does **not** overwrite
        on clamp — so it still reflects what the renderer actually counted.
        """
        llm = _mk_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        # A big-enough system-role block so its chars vs tokens are
        # unambiguous. (System-role blocks land in ``context_blocks_tokens``.)
        long_block = "the quick brown fox jumps over the lazy dog. " * 400
        agent.context_manager["prose"] = long_block

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            await agent.runtime._build_messages(method, call_args=(agent, "hi"), call_kwargs={})
        finally:
            _current_llm_var.reset(token)

        stats = agent.runtime._last_context_stats
        assert stats is not None
        # Ceiling: tokens must be well below the raw character count. At
        # ~4 chars/token, 18,400 chars should land ~4,600 tokens. Allow
        # 2× slack for block wrappers and other system blocks in the mix.
        assert stats.context_blocks_tokens < len(long_block) // 2, (
            f"context_blocks_tokens={stats.context_blocks_tokens:,} is close to "
            f"raw block chars ({len(long_block):,}) — renderer is still "
            "treating ``len`` as tokens"
        )

    def test_clamp_budget_accounts_for_tool_schemas(self):
        """Bug #2: ``_clamp_messages_to_budget`` used to call
        ``litellm.token_counter(messages=…)`` without ``tools=…``, under-
        counting by the tool-schema cost (issue #133). With the fix,
        passing ``tool_schemas=`` makes the budget larger (because the
        tool schemas are now billed against the system-message share),
        and passing the same schemas the API will see closes the gap.
        """
        from nemo_oo_agents.runtime.actor import _clamp_messages_to_budget

        model = "anthropic/claude-3-5-sonnet-20240620"
        messages = [
            {"role": "system", "content": "You are a helpful agent."},
            {"role": "user", "content": "What is 2 + 2?"},
        ]
        tool_schemas = [
            {
                "type": "function",
                "function": {
                    "name": "add",
                    "description": "Add two integers and return the sum.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "integer"},
                            "b": {"type": "integer"},
                        },
                        "required": ["a", "b"],
                    },
                },
            },
        ]

        budget = 10_000  # comfortably bigger than anything in this test
        _, total_no_tools, _, _ = _clamp_messages_to_budget(messages, budget, model)
        _, total_with_tools, _, _ = _clamp_messages_to_budget(
            messages, budget, model, tool_schemas=tool_schemas
        )
        # The tool schema has to add some tokens; it's at least a few
        # dozen (function name + description + parameter schema).
        assert total_with_tools > total_no_tools, (
            f"tool schemas must add tokens, got {total_with_tools} <= {total_no_tools}"
        )
        assert total_with_tools - total_no_tools >= 20, (
            "tool-schema overhead unexpectedly small — expected at least "
            f"~20 tokens, got {total_with_tools - total_no_tools}"
        )

class TestMaxOutputTokensBudget:
    """The safety net must account for ``max_output_tokens`` when computing
    the input budget.  With ``max_tokens=64000`` on a 131072-token window
    the old ``ctx_window × 0.70 = 91750`` cap was too generous — the real
    safe limit is ``131072 − 64000 = 67072``.  The fix passes
    ``max_output_tokens`` into ``_build_messages`` so the cap tightens.
    """

    @pytest.mark.asyncio
    async def test_large_max_tokens_tightens_budget(self):
        """Reproduces the KDD Cup crash: 131072-token window with
        max_tokens=64000.  Without the fix the safety net allows up to
        ~91K input tokens; the API rejects at 67073.  With the fix the
        cap drops to ~60K and the safety net fires before overflow.
        """
        import litellm

        from context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
        from nemo_oo_agents.events import PythonOutput

        llm = _mk_llm(131_072)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        # Pump enough events to exceed 67K input tokens but stay under 91K.
        for i in range(200):
            tc_id = f"call_{i}"
            agent.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    name="execute_python",
                    arguments={"code": "x " * 400},
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
                    stdout="y " * 400,
                    stderr="",
                    execution_status=ResultStatus.COMPLETE,
                )
            )

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        max_output_tokens = 64_000

        token = _current_llm_var.set(agent._llm)
        try:
            messages = await agent.runtime._build_messages(
                method,
                call_args=(agent, "hi"),
                call_kwargs={},
                max_output_tokens=max_output_tokens,
            )
        finally:
            _current_llm_var.reset(token)

        assert messages is not None
        structured = litellm.token_counter(model=agent._llm.model, messages=messages)
        # The invariant: input_tokens + max_output_tokens < ctx_window
        assert structured + max_output_tokens < agent._llm.context_window, (
            f"input ({structured:,}) + max_output ({max_output_tokens:,}) = "
            f"{structured + max_output_tokens:,} >= ctx_window "
            f"({agent._llm.context_window:,}) — safety net failed to account "
            "for max_output_tokens"
        )

    @pytest.mark.asyncio
    async def test_small_max_tokens_uses_default_cap(self):
        """When max_tokens is small (< 30 % of ctx_window), the default
        70 % heuristic is already tighter and should win.  No regression.
        """
        import litellm

        from nemo_oo_agents.events import Message

        llm = _mk_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for _ in range(5):
            agent.event_manager.add(Message(content="small message"))

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            messages = await agent.runtime._build_messages(
                method,
                call_args=(agent, "hi"),
                call_kwargs={},
                max_output_tokens=4096,
            )
        finally:
            _current_llm_var.reset(token)

        assert messages is not None
        stats = agent.runtime._last_context_stats
        assert stats is not None
        # Nothing should be dropped — session is tiny.
        assert stats.events_dropped == 0

    @pytest.mark.asyncio
    async def test_none_max_output_tokens_falls_back(self):
        """When max_output_tokens is None (not passed), the old 70 %
        heuristic must be used — no crash from None arithmetic.
        """
        from nemo_oo_agents.events import Message

        llm = _mk_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for _ in range(3):
            agent.event_manager.add(Message(content="test message"))

        method = type(agent).respond
        from nemo_oo_agents.runtime.actor import _current_llm_var

        token = _current_llm_var.set(agent._llm)
        try:
            messages = await agent.runtime._build_messages(
                method,
                call_args=(agent, "hi"),
                call_kwargs={},
                # max_output_tokens not passed — defaults to None
            )
        finally:
            _current_llm_var.reset(token)

        assert messages is not None

