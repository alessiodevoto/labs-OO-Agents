# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for standalone generation functions (@strategy on module-level async functions).

Covers:
- PredictStrategy: str, Pydantic, int return types
- CodeActStrategy: single-shot, multi-iteration, default strategy
- LLM resolution: explicit llm=, parent-agent cascade, missing → RuntimeError
- History isolation: fresh EventManager per call (sequential + parallel)
- exec_globals: module constants, helper functions, Pydantic return types
- CodeAct calling module-level functions and other standalone functions
- ScopedContext: context blocks in system prompt, EventQuery filtering
- Wrapper metadata: _standalone, _plan_strategy, _plan_llm, __name__, __doc__
- Error cases: non-async function, stacked decorators, invalid context type
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel

from nemo_oo_agents import EventQuery, strategy
from nemo_oo_agents.context_blocks import ScopedContext
from nemo_oo_agents.strategies import CodeActStrategy, PredictStrategy
from nemo_oo_agents.unifiedllm import FakeLLMClient, LLMResponse, ToolCall

# ---------------------------------------------------------------------------
# Module-level symbols — accessible to generated code via exec_globals
# ---------------------------------------------------------------------------

MODULE_CONSTANT = "MODULE_CONSTANT_abc123"


def double(x: int) -> int:
    """Double x — callable from generated code."""
    return x * 2


class Category(BaseModel):
    name: str
    confidence: float


# Module-level inner standalone for the CodeAct→standalone delegation test.
# No llm= so it inherits the caller's LLM via _parent_agent_var cascade.
@strategy(PredictStrategy())
async def _inner_classify(text: str) -> str:
    """Classify {text} as positive or negative."""
    ...


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class RecordingFakeLLM(FakeLLMClient):
    """FakeLLMClient that records every call, not just the last."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.all_calls: list[dict[str, Any]] = []

    async def acall(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        output_model: Any = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.all_calls.append(
            {"messages": list(messages), "tools": tools, "output_model": output_model}
        )
        return await super().acall(messages, tools, output_model, **kwargs)


def _resp(content: str = "", tool_calls: list | None = None) -> LLMResponse:
    finish_reason = "tool_calls" if tool_calls else "stop"
    return LLMResponse(
        raw_response=None,
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        assistant_message={"role": "assistant", "content": content},
    )


def _exec(code: str, call_id: str = "call_exec") -> ToolCall:
    return ToolCall(id=call_id, name="execute_python", arguments=json.dumps({"code": code}))


def _ret(result: Any, call_id: str = "call_ret") -> ToolCall:
    return ToolCall(id=call_id, name="return_result", arguments=json.dumps({"result": result}))


def _get_system_prompt(call: dict[str, Any]) -> str:
    """Extract system prompt text from a recorded LLM call dict."""
    for msg in call["messages"]:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, list):
                return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
            return content
    return ""


# ---------------------------------------------------------------------------
# Wrapper metadata / attribute tests (no LLM calls)
# ---------------------------------------------------------------------------


class TestStandaloneMetadata:
    """Wrapper attributes set by create_standalone_wrapper()."""

    def test_standalone_flag(self) -> None:
        @strategy(PredictStrategy(), llm=FakeLLMClient())
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        assert fn._standalone is True  # type: ignore[attr-defined]

    def test_needs_generation(self) -> None:
        @strategy(PredictStrategy(), llm=FakeLLMClient())
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        assert fn._needs_generation is True  # type: ignore[attr-defined]

    def test_plan_strategy_stored(self) -> None:
        strat = PredictStrategy()

        @strategy(strat, llm=FakeLLMClient())
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        assert fn._plan_strategy is strat  # type: ignore[attr-defined]

    def test_plan_llm_stored(self) -> None:
        llm = FakeLLMClient()

        @strategy(PredictStrategy(), llm=llm)
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        assert fn._plan_llm is llm  # type: ignore[attr-defined]

    def test_wraps_preserves_name(self) -> None:
        @strategy(PredictStrategy(), llm=FakeLLMClient())
        async def my_standalone_function(x: str) -> str:
            """Process {x}."""
            ...

        assert my_standalone_function.__name__ == "my_standalone_function"

    def test_wraps_preserves_doc(self) -> None:
        @strategy(PredictStrategy(), llm=FakeLLMClient())
        async def fn(x: str) -> str:
            """My custom docstring for {x}."""
            ...

        assert fn.__doc__ == "My custom docstring for {x}."

    def test_default_strategy_is_codeact(self) -> None:
        """No explicit strategy → CodeActStrategy (framework default)."""

        @strategy(llm=FakeLLMClient())
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        assert isinstance(fn._plan_strategy, CodeActStrategy)  # type: ignore[attr-defined]

    def test_no_ellipsis_not_wrapped(self) -> None:
        """A standalone function without ellipsis body is returned unchanged."""

        @strategy(PredictStrategy())
        async def fn(x: str) -> str:
            return "static"

        assert not hasattr(fn, "_standalone")


# ---------------------------------------------------------------------------
# Validation / error-path tests
# ---------------------------------------------------------------------------


class TestStandaloneErrors:
    def test_non_async_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="must be async"):

            @strategy(PredictStrategy(), llm=FakeLLMClient())
            def sync_fn(x: str) -> str:  # type: ignore[arg-type]
                """Sync {x}."""
                ...

    @pytest.mark.asyncio
    async def test_no_llm_raises_runtimeerror(self) -> None:
        @strategy(PredictStrategy())
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        with pytest.raises(RuntimeError, match="No LLM client"):
            await fn("test")

    def test_stacked_decorators_raise_valueerror(self) -> None:
        with pytest.raises(ValueError, match="Cannot stack multiple @strategy"):

            @strategy(PredictStrategy())
            @strategy(PredictStrategy(), llm=FakeLLMClient())
            async def fn(x: str) -> str:
                """Double-decorated {x}."""
                ...

    def test_invalid_context_type_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="must be ScopedContext"):

            @strategy(context={"invalid": "dict"})  # type: ignore[arg-type]
            async def fn(x: str) -> str:
                """Process {x}."""
                ...


# ---------------------------------------------------------------------------
# PredictStrategy execution
# ---------------------------------------------------------------------------


class TestStandalonePredictStrategy:
    @pytest.mark.asyncio
    async def test_str_return(self) -> None:
        fake_llm = FakeLLMClient(scripted_responses=[_resp('{"value": "positive"}')])

        @strategy(PredictStrategy(), llm=fake_llm)
        async def classify(text: str) -> str:
            """Classify the sentiment of {text}."""
            ...

        assert await classify("I love this!") == "positive"

    @pytest.mark.asyncio
    async def test_pydantic_return(self) -> None:
        fake_llm = FakeLLMClient(
            scripted_responses=[_resp('{"name": "billing", "confidence": 0.95}')]
        )

        @strategy(PredictStrategy(), llm=fake_llm)
        async def categorize(text: str) -> Category:
            """Categorize the support ticket: {text}."""
            ...

        result = await categorize("I was charged twice")
        assert isinstance(result, Category)
        assert result.name == "billing"
        assert result.confidence == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_int_return(self) -> None:
        fake_llm = FakeLLMClient(scripted_responses=[_resp('{"value": 7}')])

        @strategy(PredictStrategy(), llm=fake_llm)
        async def count_words(text: str) -> int:
            """Count the words in {text}."""
            ...

        assert await count_words("one two three") == 7

    @pytest.mark.asyncio
    async def test_sequential_calls_use_independent_responses(self) -> None:
        fake_llm = FakeLLMClient(
            scripted_responses=[
                _resp('{"value": "alpha"}'),
                _resp('{"value": "beta"}'),
            ]
        )

        @strategy(PredictStrategy(), llm=fake_llm)
        async def label(x: str) -> str:
            """Label {x}."""
            ...

        r1 = await label("first")
        r2 = await label("second")
        assert r1 == "alpha"
        assert r2 == "beta"
        assert fake_llm.call_count == 2


# ---------------------------------------------------------------------------
# CodeActStrategy execution
# ---------------------------------------------------------------------------


class TestStandaloneCodeActStrategy:
    @pytest.mark.asyncio
    async def test_single_shot_return(self) -> None:
        fake_llm = FakeLLMClient(
            scripted_responses=[_resp(tool_calls=[_exec('return_result(result="done")')])]
        )

        @strategy(CodeActStrategy(), llm=fake_llm)
        async def process(data: str) -> str:
            """Process {data}."""
            ...

        assert await process("input") == "done"

    @pytest.mark.asyncio
    async def test_multi_iteration(self) -> None:
        """execute_python → observe output → return_result."""
        fake_llm = FakeLLMClient(
            scripted_responses=[
                _resp(tool_calls=[_exec("x = 3 + 4\nprint(x)")]),
                _resp(tool_calls=[_ret(7)]),
            ]
        )

        @strategy(CodeActStrategy(), llm=fake_llm)
        async def compute(expr: str) -> int:
            """Compute {expr} and return the result."""
            ...

        assert await compute("3+4") == 7

    @pytest.mark.asyncio
    async def test_default_strategy_executes(self) -> None:
        """@strategy() with no strategy arg uses CodeActStrategy."""
        fake_llm = FakeLLMClient(
            scripted_responses=[_resp(tool_calls=[_exec('return_result(result="default_ok")')])]
        )

        @strategy(llm=fake_llm)
        async def process(data: str) -> str:
            """Process {data}."""
            ...

        assert await process("x") == "default_ok"

    @pytest.mark.asyncio
    async def test_calls_module_level_function(self) -> None:
        """Generated code can call module-level functions via exec_globals."""
        fake_llm = FakeLLMClient(
            scripted_responses=[_resp(tool_calls=[_exec("return_result(result=double(5))")])]
        )

        @strategy(CodeActStrategy(), llm=fake_llm)
        async def compute(n: int) -> int:
            """Double {n} using the helper."""
            ...

        # double(5) == 10 — the module-level function runs inside execute_python
        assert await compute(5) == 10

    @pytest.mark.asyncio
    async def test_accesses_module_constant(self) -> None:
        """Generated code can read module-level constants via exec_globals."""
        fake_llm = FakeLLMClient(
            scripted_responses=[_resp(tool_calls=[_exec("return_result(result=MODULE_CONSTANT)")])]
        )

        @strategy(CodeActStrategy(), llm=fake_llm)
        async def get_const() -> str:
            """Return the module-level constant."""
            ...

        assert await get_const() == MODULE_CONSTANT

    @pytest.mark.asyncio
    async def test_pydantic_model_as_return_type(self) -> None:
        """Module-level Pydantic model resolves correctly as CodeAct return type."""
        fake_llm = FakeLLMClient(
            scripted_responses=[
                _resp(
                    tool_calls=[_exec('return_result(result={"name": "tech", "confidence": 0.9})')]
                )
            ]
        )

        @strategy(CodeActStrategy(), llm=fake_llm)
        async def categorize(text: str) -> Category:
            """Categorize {text}."""
            ...

        result = await categorize("AI research paper")
        assert isinstance(result, Category)
        assert result.name == "tech"

    @pytest.mark.asyncio
    async def test_calls_inner_standalone_function(self) -> None:
        """Generated code can call another standalone generation function.

        _inner_classify is defined at module level with no llm=.  When called
        from within CodeAct's execute_python, _parent_agent_var is the outer
        standalone's agent stub, so _inner_classify inherits its LLM via cascade.
        """
        fake_llm = FakeLLMClient(
            scripted_responses=[
                # CodeAct turn: call _inner_classify, then return its result
                _resp(
                    tool_calls=[
                        _exec(
                            'result = await _inner_classify("great service")\n'
                            "return_result(result=result)"
                        )
                    ]
                ),
                # PredictStrategy response for _inner_classify
                _resp('{"value": "positive"}'),
            ]
        )

        @strategy(CodeActStrategy(), llm=fake_llm)
        async def classify_outer(text: str) -> str:
            """Classify {text} by delegating to _inner_classify."""
            ...

        result = await classify_outer("great service")
        assert result == "positive"
        assert fake_llm.call_count == 2


# ---------------------------------------------------------------------------
# LLM resolution cascade
# ---------------------------------------------------------------------------


class TestStandaloneLLMCascade:
    @pytest.mark.asyncio
    async def test_explicit_llm_used(self) -> None:
        explicit_llm = FakeLLMClient(scripted_responses=[_resp('{"value": "explicit"}')])

        @strategy(PredictStrategy(), llm=explicit_llm)
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        assert await fn("test") == "explicit"
        assert explicit_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_parent_agent_llm_inherited(self) -> None:
        """When llm= is absent, LLM is inherited from _parent_agent_var."""
        from nemo_oo_agents.runtime.context_vars import _parent_agent_var

        parent_llm = FakeLLMClient(scripted_responses=[_resp('{"value": "from_parent"}')])

        class _FakeAgent:
            _llm = parent_llm

        @strategy(PredictStrategy())
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        token = _parent_agent_var.set(_FakeAgent())  # type: ignore[arg-type]
        try:
            result = await fn("test")
        finally:
            _parent_agent_var.reset(token)

        assert result == "from_parent"
        assert parent_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_explicit_llm_overrides_parent(self) -> None:
        """Explicit llm= takes priority over the parent-agent cascade."""
        from nemo_oo_agents.runtime.context_vars import _parent_agent_var

        explicit_llm = FakeLLMClient(scripted_responses=[_resp('{"value": "explicit"}')])
        parent_llm = FakeLLMClient(scripted_responses=[_resp('{"value": "parent"}')])

        class _FakeAgent:
            _llm = parent_llm

        @strategy(PredictStrategy(), llm=explicit_llm)
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        token = _parent_agent_var.set(_FakeAgent())  # type: ignore[arg-type]
        try:
            result = await fn("test")
        finally:
            _parent_agent_var.reset(token)

        assert result == "explicit"
        assert explicit_llm.call_count == 1
        assert parent_llm.call_count == 0

    @pytest.mark.asyncio
    async def test_no_llm_no_parent_raises(self) -> None:
        @strategy(PredictStrategy())
        async def fn(x: str) -> str:
            """Process {x}."""
            ...

        with pytest.raises(RuntimeError, match="No LLM client"):
            await fn("test")


# ---------------------------------------------------------------------------
# History isolation — fresh EventManager per call
# ---------------------------------------------------------------------------


class TestStandaloneHistoryIsolation:
    @pytest.mark.asyncio
    async def test_sequential_calls_no_cross_history(self) -> None:
        """Second call must not see Task events from the first call."""
        CALL1 = "HISTORY_ISO_FIRST_xq7z"
        CALL2 = "HISTORY_ISO_SECOND_m3w9"

        recording_llm = RecordingFakeLLM(
            scripted_responses=[
                _resp('{"value": "r1"}'),
                _resp('{"value": "r2"}'),
            ]
        )

        @strategy(PredictStrategy(), llm=recording_llm)
        async def fn(text: str) -> str:
            """Classify {text}."""
            ...

        await fn(CALL1)
        await fn(CALL2)

        assert len(recording_llm.all_calls) == 2
        call2_messages = json.dumps(recording_llm.all_calls[1]["messages"])
        # If EventManager were shared, CALL1 would appear in call 2's history.
        assert CALL1 not in call2_messages, (
            "First call's task text leaked into second call — EventManager not fresh per call."
        )
        assert CALL2 in call2_messages

    @pytest.mark.asyncio
    async def test_parallel_calls_each_get_own_result(self) -> None:
        """asyncio.gather: each concurrent call produces an independent result."""
        recording_llm = RecordingFakeLLM(
            scripted_responses=[
                _resp('{"value": "p1"}'),
                _resp('{"value": "p2"}'),
                _resp('{"value": "p3"}'),
            ]
        )

        @strategy(PredictStrategy(), llm=recording_llm)
        async def fn(text: str) -> str:
            """Classify {text}."""
            ...

        results = await asyncio.gather(fn("a"), fn("b"), fn("c"))
        assert sorted(results) == ["p1", "p2", "p3"]
        assert recording_llm.call_count == 3

    @pytest.mark.asyncio
    async def test_repeated_calls_correct_independent_results(self) -> None:
        """Each call gets a fresh agent stub — no state accumulation across calls."""
        fake_llm = FakeLLMClient(
            scripted_responses=[
                _resp('{"value": "x"}'),
                _resp('{"value": "y"}'),
                _resp('{"value": "z"}'),
            ]
        )

        @strategy(PredictStrategy(), llm=fake_llm)
        async def fn(text: str) -> str:
            """Process {text}."""
            ...

        assert await fn("first") == "x"
        assert await fn("second") == "y"
        assert await fn("third") == "z"


# ---------------------------------------------------------------------------
# ScopedContext: context blocks and event filtering
# ---------------------------------------------------------------------------


class TestStandaloneScopedContext:
    @pytest.mark.asyncio
    async def test_context_block_in_system_prompt(self) -> None:
        """ScopedContext(context={...}) injects the key/value into the system prompt."""
        MARKER = "CONTEXT_BLOCK_MARKER_u8v2k"
        recording_llm = RecordingFakeLLM(scripted_responses=[_resp('{"value": "ok"}')])

        @strategy(
            PredictStrategy(),
            ScopedContext(context={"role": MARKER}),
            llm=recording_llm,
        )
        async def fn(text: str) -> str:
            """Process {text}."""
            ...

        await fn("some input")

        assert len(recording_llm.all_calls) == 1
        system_prompt = _get_system_prompt(recording_llm.all_calls[0])
        assert MARKER in system_prompt, (
            f"Context marker '{MARKER}' missing from system prompt.\n"
            f"System prompt (first 400 chars): {system_prompt[:400]}"
        )

    @pytest.mark.asyncio
    async def test_context_block_present_on_every_call(self) -> None:
        """Decorator context blocks appear on each independent call."""
        MARKER = "PER_CALL_CONTEXT_n4f7"
        recording_llm = RecordingFakeLLM(
            scripted_responses=[
                _resp('{"value": "r1"}'),
                _resp('{"value": "r2"}'),
            ]
        )

        @strategy(
            PredictStrategy(),
            ScopedContext(context={"note": MARKER}),
            llm=recording_llm,
        )
        async def fn(text: str) -> str:
            """Process {text}."""
            ...

        await fn("first")
        await fn("second")

        assert len(recording_llm.all_calls) == 2
        for i, call in enumerate(recording_llm.all_calls):
            system_prompt = _get_system_prompt(call)
            assert MARKER in system_prompt, f"Call {i}: context marker missing from system prompt"

    @pytest.mark.asyncio
    async def test_event_query_current_call_executes(self) -> None:
        """ScopedContext(events=EventQuery.current_call()) runs without error."""
        fake_llm = FakeLLMClient(
            scripted_responses=[_resp(tool_calls=[_exec('return_result(result="ok")')])]
        )

        @strategy(
            CodeActStrategy(),
            ScopedContext(events=EventQuery.current_call()),
            llm=fake_llm,
        )
        async def fn(text: str) -> str:
            """Process {text}."""
            ...

        assert await fn("test") == "ok"

    @pytest.mark.asyncio
    async def test_context_and_events_together(self) -> None:
        """ScopedContext with both context and events works end-to-end."""
        CTX_MARKER = "COMBINED_CTX_MARKER_z3r1"
        fake_llm = FakeLLMClient(
            scripted_responses=[_resp(tool_calls=[_exec('return_result(result="combined")')])]
        )

        @strategy(
            CodeActStrategy(),
            ScopedContext(
                context={"hint": CTX_MARKER},
                events=EventQuery.current_call(),
            ),
            llm=fake_llm,
        )
        async def fn(text: str) -> str:
            """Process {text}."""
            ...

        assert await fn("input") == "combined"
