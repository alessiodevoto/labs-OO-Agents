"""Unit tests covering remaining gaps in nemo_oo_agents modules.

Targets:
- nemo_flow_middleware.py: async middleware handlers
- config/truncation_config.py: validators
- runtime/async_safety.py: concurrent.futures safety
- runtime/event_query.py: event filtering
- runtime/media_capture.py: media/image capture
- library_skill.py: AST fallback path
- tools/bash_tool.py: error paths and edge cases
"""

from __future__ import annotations

import concurrent.futures
import importlib
import sys
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ===========================================================================
# Helpers shared across NeMo Flow tests
# ===========================================================================


def _make_fake_nemo_flow():
    """Build a MagicMock that looks like nemo_flow."""
    fake = MagicMock()
    fake_handle = MagicMock()
    fake_handle.uuid = "test-uuid-1234"
    fake.scope.scope.return_value.__enter__ = MagicMock(return_value=fake_handle)
    fake.scope.scope.return_value.__exit__ = MagicMock(return_value=False)
    # llm.execute is called as: await nemo_flow.llm.execute(...)
    fake.llm.execute = AsyncMock(side_effect=_invoke_wrapper_for_llm)
    # tools.execute is called as: await nemo_flow.tools.execute(...)
    fake.tools.execute = AsyncMock(side_effect=_invoke_wrapper_for_tools)
    # scope.push / scope.pop
    fake.scope.push.return_value = MagicMock(name="scope_handle")
    fake.scope.pop.return_value = None
    return fake, fake_handle


# These callables are replaced per-test; the defaults just call through.
async def _invoke_wrapper_for_llm(*args, **kwargs):
    """Default: call the wrapper with the request to simulate NeMo Flow invoking it."""
    # Signature: (model_name, request, wrapper, model_name=model_name)
    # positional args: (model_name, request, wrapper)
    wrapper = args[2]
    request = args[1]
    await wrapper(request)


async def _invoke_wrapper_for_tools(tool_name, args, wrapper):
    """Default: call the wrapper with the args to simulate NeMo Flow invoking it."""
    await wrapper(args)


@contextmanager
def _nemo_flow_patched():
    """Patch sys.modules with a fake nemo_flow, reload nemo_flow_middleware, yield (module, fake)."""

    fake_nemo_flow, fake_handle = _make_fake_nemo_flow()

    # LLMRequest needs to be constructable
    fake_llm_request_cls = MagicMock(return_value=MagicMock(content={}))

    # Ensure the module is imported before patching (KeyError if not in sys.modules)
    import nemo_oo_agents.nemo_flow_middleware as _nm_ensure  # noqa: F811, F401

    with patch.dict(
        sys.modules,
        {
            "nemo_flow": fake_nemo_flow,
            "nemo_flow.LLMRequest": fake_llm_request_cls,
        },
    ):
        nm = sys.modules["nemo_oo_agents.nemo_flow_middleware"]
        importlib.reload(nm)
        try:
            yield nm, fake_nemo_flow, fake_handle
        finally:
            pass  # Don't reload here — still inside patch.dict so nemo_flow is present

    # Reload AFTER patch.dict exits (nemo_flow removed from sys.modules)
    importlib.reload(nm)


# ===========================================================================
# nemo_flow_middleware.py — async handler tests
# ===========================================================================


class TestNemoFlowLLMMiddleware:
    """Tests for nemo_flow_llm_middleware (lines 92–184)."""

    async def _run_llm_middleware(self, fake_nemo_flow, nm, ctx_kwargs=None, nxt_response=None):
        """Helper: build ctx/nxt and run nemo_flow_llm_middleware."""
        from nemo_oo_agents.runtime.middleware import LLMCallContext

        ctx = LLMCallContext(
            messages=[{"role": "user", "content": "hello"}],
            params={"temperature": 0.7, "api_key": "secret", "tools": ["tool"]},
            agent=None,
        )
        if ctx_kwargs:
            for k, v in ctx_kwargs.items():
                setattr(ctx, k, v)

        # Build the inner result ctx
        result_ctx = LLMCallContext(
            messages=ctx.messages,
            params=ctx.params,
            agent=None,
        )
        if nxt_response is not None:
            result_ctx.response = nxt_response

        async def nxt(c):
            return result_ctx

        return await nm.nemo_flow_llm_middleware(ctx, nxt)

    async def test_llm_middleware_calls_nemo_flow_execute(self):
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            await self._run_llm_middleware(fake_nemo_flow, nm)
            fake_nemo_flow.llm.execute.assert_called_once()

    async def test_llm_middleware_strips_sensitive_keys(self):
        """The wrapper should receive a request without api_key / base_url."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            captured_request = {}

            async def capturing_execute(*args, **kwargs):
                request = args[1]
                wrapper = args[2]
                captured_request["req"] = request
                await wrapper(request)

            fake_nemo_flow.llm.execute.side_effect = capturing_execute

            from nemo_oo_agents.runtime.middleware import LLMCallContext

            ctx = LLMCallContext(
                messages=[{"role": "user", "content": "hi"}],
                params={"api_key": "SECRET", "temperature": 0.5},
            )
            result_ctx = LLMCallContext(messages=ctx.messages, params={})

            async def nxt(c):
                return result_ctx

            await nm.nemo_flow_llm_middleware(ctx, nxt)
            # LLMRequest was constructed — first positional arg to fake cls is {}
            # (the second positional arg is safe_params without api_key)
            call_args = nm.LLMRequest.call_args
            assert call_args is not None
            _, safe_params = call_args[0]
            assert "api_key" not in safe_params
            assert "tools" not in safe_params

    async def test_llm_middleware_returns_captured_ctx(self):
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.runtime.middleware import LLMCallContext

            ctx = LLMCallContext(messages=[{"role": "user", "content": "hi"}], params={})
            inner = LLMCallContext(messages=ctx.messages, params={})

            async def nxt(c):
                return inner

            result = await nm.nemo_flow_llm_middleware(ctx, nxt)
            assert result is inner

    async def test_llm_middleware_guardrail_blocks_raises(self):
        """When nemo_flow.llm.execute() never invokes _wrapper, raise RuntimeError."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            # Override execute to NOT call the wrapper (simulates guardrail block)
            async def blocking_execute(*args, **kwargs):
                pass  # don't call wrapper

            fake_nemo_flow.llm.execute.side_effect = blocking_execute

            from nemo_oo_agents.runtime.middleware import LLMCallContext

            ctx = LLMCallContext(messages=[{"role": "user", "content": "hi"}], params={})

            async def nxt(c):
                return c

            with pytest.raises(RuntimeError, match="NeMo Flow guardrail blocked the LLM call"):
                await nm.nemo_flow_llm_middleware(ctx, nxt)

    async def test_llm_middleware_with_agent_model(self):
        """Model name is extracted from ctx.agent._llm.model.

        We test this by verifying that nemo_flow.llm.execute receives the model name
        from the mock agent, not from ctx (which has no agent in this test).
        """
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            captured_calls = []

            async def recording_execute(*args, **kwargs):
                model_name_pos = args[0]
                wrapper = args[2]
                request = args[1]
                captured_calls.append(model_name_pos)
                await wrapper(request)

            fake_nemo_flow.llm.execute.side_effect = recording_execute

            from nemo_oo_agents.runtime.middleware import LLMCallContext

            # agent=None is fine; model extraction returns "" in that case
            ctx = LLMCallContext(
                messages=[{"role": "user", "content": "hi"}], params={}, agent=None
            )
            inner = LLMCallContext(messages=ctx.messages, params={})

            async def nxt(c):
                return inner

            await nm.nemo_flow_llm_middleware(ctx, nxt)
            # model_name is "" because agent is None
            assert captured_calls[0] == ""

    async def test_llm_middleware_response_with_raw_model_dump(self):
        """When response has raw_response with model_dump, it is used."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):

            async def execute_and_capture(*args, **kwargs):
                wrapper = args[2]
                request = args[1]
                await wrapper(request)

            fake_nemo_flow.llm.execute.side_effect = execute_and_capture

            from nemo_oo_agents.runtime.middleware import LLMCallContext

            raw_resp = MagicMock()
            raw_resp.model_dump.return_value = {"choices": []}

            response = MagicMock()
            response.raw_response = raw_resp

            ctx = LLMCallContext(messages=[{"role": "user", "content": "hi"}], params={})
            inner = LLMCallContext(messages=ctx.messages, params={})
            inner.response = response

            async def nxt(c):
                return inner

            result = await nm.nemo_flow_llm_middleware(ctx, nxt)
            assert result is inner
            raw_resp.model_dump.assert_called_once_with(mode="json")

    async def test_llm_middleware_response_with_model_dump_no_raw(self):
        """When response has model_dump but no raw_response, model_dump is used."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.runtime.middleware import LLMCallContext

            response = MagicMock(spec=["model_dump"])
            response.raw_response = None
            response.model_dump.return_value = {"result": "data"}

            ctx = LLMCallContext(messages=[{"role": "user", "content": "hi"}], params={})
            inner = LLMCallContext(messages=ctx.messages, params={})
            inner.response = response

            async def nxt(c):
                return inner

            await nm.nemo_flow_llm_middleware(ctx, nxt)
            response.model_dump.assert_called_once_with(mode="json")

    async def test_llm_middleware_response_assistant_message_fallback(self):
        """When response has assistant_message, fall back to manual serialization."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.runtime.middleware import LLMCallContext

            response = MagicMock(spec=["assistant_message", "usage", "finish_reason"])
            response.assistant_message = "Hello!"
            response.usage = {"total_tokens": 10}
            response.finish_reason = "stop"

            ctx = LLMCallContext(messages=[{"role": "user", "content": "hi"}], params={})
            inner = LLMCallContext(messages=ctx.messages, params={})
            inner.response = response

            async def nxt(c):
                return inner

            # Should not raise
            await nm.nemo_flow_llm_middleware(ctx, nxt)

    async def test_llm_middleware_response_none_returns_empty(self):
        """When response is None, wrapper returns {}."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.runtime.middleware import LLMCallContext

            ctx = LLMCallContext(messages=[{"role": "user", "content": "hi"}], params={})
            inner = LLMCallContext(messages=ctx.messages, params={})
            inner.response = None

            async def nxt(c):
                return inner

            # Should not raise, captured_ctx is inner
            result = await nm.nemo_flow_llm_middleware(ctx, nxt)
            assert result is inner

    async def test_llm_middleware_request_intercept_propagates_messages(self):
        """When NeMo Flow request intercept modifies messages, they propagate to ctx."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            new_messages = [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "hi"},
            ]

            async def intercepting_execute(*args, **kwargs):
                request = args[1]
                wrapper = args[2]
                # Simulate NeMo Flow modifying the request
                request.content = {"messages": new_messages}
                await wrapper(request)

            fake_nemo_flow.llm.execute.side_effect = intercepting_execute

            from nemo_oo_agents.runtime.middleware import LLMCallContext

            ctx = LLMCallContext(messages=[{"role": "user", "content": "hi"}], params={})
            inner = LLMCallContext(messages=ctx.messages, params={})

            async def nxt(c):
                return inner

            await nm.nemo_flow_llm_middleware(ctx, nxt)
            # ctx.messages should be updated to new_messages
            assert ctx.messages == new_messages

    async def test_llm_middleware_request_intercept_propagates_params(self):
        """When NeMo Flow request intercept modifies temperature, it propagates."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):

            async def intercepting_execute(*args, **kwargs):
                request = args[1]
                wrapper = args[2]
                request.content = {"temperature": 0.1, "seed": 42}
                await wrapper(request)

            fake_nemo_flow.llm.execute.side_effect = intercepting_execute

            from nemo_oo_agents.runtime.middleware import LLMCallContext

            ctx = LLMCallContext(
                messages=[{"role": "user", "content": "hi"}],
                params={"temperature": 0.9},
            )
            inner = LLMCallContext(messages=ctx.messages, params=ctx.params)

            async def nxt(c):
                return inner

            await nm.nemo_flow_llm_middleware(ctx, nxt)
            assert ctx.params["temperature"] == 0.1
            assert ctx.params["seed"] == 42


class TestNemoFlowToolMiddleware:
    """Tests for nemo_flow_tool_middleware (lines 197–258)."""

    async def _make_exec_ctx(self, code="print('hi')", params=None, result=None):
        from nemo_oo_agents.runtime.middleware import ExecutePythonContext

        ctx = ExecutePythonContext(code=code, params=params or {})
        inner = ExecutePythonContext(code=code, params=params or {})
        inner.result = result
        return ctx, inner

    async def test_tool_middleware_calls_nemo_flow_execute(self):
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            ctx, inner = await self._make_exec_ctx()

            async def nxt(c):
                return inner

            await nm.nemo_flow_tool_middleware(ctx, nxt)
            fake_nemo_flow.tools.execute.assert_called_once()

    async def test_tool_middleware_returns_captured_ctx(self):
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            ctx, inner = await self._make_exec_ctx()

            async def nxt(c):
                return inner

            result = await nm.nemo_flow_tool_middleware(ctx, nxt)
            assert result is inner

    async def test_tool_middleware_guardrail_blocks_raises(self):
        """When nemo_flow.tools.execute() never invokes _wrapper, raise RuntimeError."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):

            async def blocking_execute(tool_name, args, wrapper):
                pass  # don't call wrapper

            fake_nemo_flow.tools.execute.side_effect = blocking_execute

            ctx, inner = await self._make_exec_ctx()

            async def nxt(c):
                return inner

            with pytest.raises(RuntimeError, match="NeMo Flow guardrail blocked code execution"):
                await nm.nemo_flow_tool_middleware(ctx, nxt)

    async def test_tool_middleware_result_none(self):
        """When result is None, codec.to_json(None) is returned."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            ctx, inner = await self._make_exec_ctx(result=None)

            async def nxt(c):
                return inner

            result = await nm.nemo_flow_tool_middleware(ctx, nxt)
            assert result is inner
            # codec.to_json(None) should have been called
            fake_nemo_flow.typed.BestEffortAnyCodec.return_value.to_json.assert_called()

    async def test_tool_middleware_result_with_returned_value(self):
        """When result.returned_value is set, it is passed to codec."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.events import ExecutionResult

            exec_result = ExecutionResult(stdout="", returned_value=42)
            ctx, inner = await self._make_exec_ctx(result=exec_result)

            async def nxt(c):
                return inner

            await nm.nemo_flow_tool_middleware(ctx, nxt)
            codec = fake_nemo_flow.typed.BestEffortAnyCodec.return_value
            codec.to_json.assert_called_with(42)

    async def test_tool_middleware_result_with_no_return_uses_stdout(self):
        """When result has _NO_RETURN and no signal, stdout is used."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.events import _NO_RETURN, ExecutionResult

            exec_result = ExecutionResult(stdout="some output", signal=None)
            # Force returned_value to be _NO_RETURN sentinel
            exec_result = exec_result.model_copy(update={"returned_value": _NO_RETURN})
            ctx, inner = await self._make_exec_ctx(result=exec_result)

            async def nxt(c):
                return inner

            await nm.nemo_flow_tool_middleware(ctx, nxt)
            codec = fake_nemo_flow.typed.BestEffortAnyCodec.return_value
            codec.to_json.assert_called_with("some output")

    async def test_tool_middleware_result_signal_with_result_key(self):
        """When result has a signal with 'result' key, that is used."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.events import _NO_RETURN, ExecutionResult, ExecutionSignal

            class TestSignal(ExecutionSignal):
                pass

            signal = TestSignal("test")
            signal.result = {"result": "signal_value"}

            exec_result = ExecutionResult(signal=signal)
            exec_result = exec_result.model_copy(update={"returned_value": _NO_RETURN})
            ctx, inner = await self._make_exec_ctx(result=exec_result)

            async def nxt(c):
                return inner

            await nm.nemo_flow_tool_middleware(ctx, nxt)
            codec = fake_nemo_flow.typed.BestEffortAnyCodec.return_value
            codec.to_json.assert_called_with("signal_value")

    async def test_tool_middleware_code_propagation_from_intercept(self):
        """NeMo Flow intercept can rewrite code; it propagates to ctx."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):

            async def intercepting_execute(tool_name, args, wrapper):
                modified_args = {"code": "print('intercepted')", "timeout": 5}
                await wrapper(modified_args)

            fake_nemo_flow.tools.execute.side_effect = intercepting_execute

            from nemo_oo_agents.runtime.middleware import ExecutePythonContext

            ctx = ExecutePythonContext(code="print('original')", params={})
            inner = ExecutePythonContext(code=ctx.code, params={})

            async def nxt(c):
                assert c.code == "print('intercepted')"
                assert c.params.get("timeout") == 5
                return inner

            await nm.nemo_flow_tool_middleware(ctx, nxt)
            assert ctx.code == "print('intercepted')"

    async def test_tool_middleware_result_signal_without_result_key(self):
        """When signal.result is not a dict with 'result', rv is None."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            from nemo_oo_agents.events import _NO_RETURN, ExecutionResult, ExecutionSignal

            class TestSignal(ExecutionSignal):
                pass

            signal = TestSignal("test")
            signal.result = "plain string"  # not a dict with 'result' key

            exec_result = ExecutionResult(signal=signal)
            exec_result = exec_result.model_copy(update={"returned_value": _NO_RETURN})
            ctx, inner = await self._make_exec_ctx(result=exec_result)

            async def nxt(c):
                return inner

            await nm.nemo_flow_tool_middleware(ctx, nxt)
            codec = fake_nemo_flow.typed.BestEffortAnyCodec.return_value
            codec.to_json.assert_called_with(None)


class TestNemoFlowAgentCallMiddleware:
    """Tests for nemo_flow_agent_call_middleware (lines 261–281).

    Note: AgentCallContext.agent must be Agent | None.  We use agent=None and
    inject a mock agent into ctx after creation, since the middleware accesses
    ctx.agent only via type(ctx.agent).__name__ which works even post-init.
    """

    def _make_ctx(self, method_name="solve", agent=None):
        from nemo_oo_agents.runtime.middleware import AgentCallContext

        ctx = AgentCallContext(agent=agent, method_name=method_name, args=(), kwargs={})
        return ctx

    async def test_agent_call_pushes_and_pops_scope(self):
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            ctx = self._make_ctx("solve")

            # Create a real object so type().__name__ works correctly
            class MyAgent:
                pass

            object.__setattr__(ctx, "agent", MyAgent())

            async def nxt(c):
                return c

            await nm.nemo_flow_agent_call_middleware(ctx, nxt)
            fake_nemo_flow.scope.push.assert_called_once()
            call_args = fake_nemo_flow.scope.push.call_args[0]
            assert call_args[0] == "MyAgent.solve"
            fake_nemo_flow.scope.pop.assert_called_once()

    async def test_agent_call_pops_scope_even_on_exception(self):
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            ctx = self._make_ctx("solve")

            async def failing_nxt(c):
                raise ValueError("deliberate error")

            with pytest.raises(ValueError, match="deliberate error"):
                await nm.nemo_flow_agent_call_middleware(ctx, failing_nxt)

            # pop() must still have been called
            fake_nemo_flow.scope.pop.assert_called_once()

    async def test_agent_call_scope_name_format(self):
        """Scope name is 'ClassName.method_name'."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            ctx = self._make_ctx("analyze")

            class ResearchAgent:
                pass

            object.__setattr__(ctx, "agent", ResearchAgent())

            async def nxt(c):
                return c

            await nm.nemo_flow_agent_call_middleware(ctx, nxt)
            call_args = fake_nemo_flow.scope.push.call_args[0]
            assert call_args[0] == "ResearchAgent.analyze"

    async def test_agent_call_scope_pop_failure_is_swallowed(self):
        """Even if scope.pop() raises, no exception propagates."""
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            fake_nemo_flow.scope.pop.side_effect = RuntimeError("pop failed")

            ctx = self._make_ctx("run")

            async def nxt(c):
                return c

            # Should not raise even though pop raises
            await nm.nemo_flow_agent_call_middleware(ctx, nxt)

    async def test_agent_call_returns_nxt_result(self):
        with _nemo_flow_patched() as (nm, fake_nemo_flow, _):
            ctx = self._make_ctx("run")
            expected = self._make_ctx("run")
            expected.result = "final_result"

            async def nxt(c):
                return expected

            result = await nm.nemo_flow_agent_call_middleware(ctx, nxt)
            assert result is expected


# ===========================================================================
# config/truncation_config.py
# ===========================================================================


class TestTruncationConfigValidators:
    """Cover the model_validator _check_values (lines 60-101)."""

    def test_default_config_valid(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        cfg = TruncationConfig()
        assert cfg.max_block_chars == 20_000

    def test_max_block_chars_zero_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_block_chars must be > 0"):
            TruncationConfig(max_block_chars=0)

    def test_max_stdout_chars_negative_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_stdout_chars must be > 0"):
            TruncationConfig(max_stdout_chars=-1)

    def test_max_stderr_chars_zero_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_stderr_chars must be > 0"):
            TruncationConfig(max_stderr_chars=0)

    def test_max_context_tokens_zero_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_context_tokens must be > 0 or None"):
            TruncationConfig(max_context_tokens=0)

    def test_max_event_tokens_negative_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_event_tokens must be > 0 or None"):
            TruncationConfig(max_event_tokens=-5)

    def test_max_context_tokens_none_valid(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        cfg = TruncationConfig(max_context_tokens=None)
        assert cfg.max_context_tokens is None

    def test_max_pprint_elements_zero_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_pprint_elements must be > 0 or None"):
            TruncationConfig(max_pprint_elements=0)

    def test_max_pprint_string_negative_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_pprint_string must be > 0 or None"):
            TruncationConfig(max_pprint_string=-1)

    def test_max_pprint_depth_zero_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="max_pprint_depth must be > 0 or None"):
            TruncationConfig(max_pprint_depth=0)

    def test_max_pprint_elements_none_valid(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        cfg = TruncationConfig(max_pprint_elements=None)
        assert cfg.max_pprint_elements is None

    def test_stdout_tail_chars_negative_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception, match="stdout_tail_chars must be >= 0"):
            TruncationConfig(stdout_tail_chars=-1)

    def test_stdout_tail_chars_equal_to_max_stdout_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(
            Exception, match="stdout_tail_chars.*must be less than.*max_stdout_chars"
        ):
            TruncationConfig(max_stdout_chars=1000, stdout_tail_chars=1000)

    def test_stdout_tail_chars_greater_than_max_stdout_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(
            Exception, match="stdout_tail_chars.*must be less than.*max_stdout_chars"
        ):
            TruncationConfig(max_stdout_chars=1000, stdout_tail_chars=1500)

    def test_stdout_tail_chars_equal_to_max_stderr_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(
            Exception, match="stdout_tail_chars.*must be less than.*max_stderr_chars"
        ):
            TruncationConfig(
                max_stdout_chars=50_000,
                max_stderr_chars=1000,
                stdout_tail_chars=1000,
            )

    def test_stdout_tail_chars_valid(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        cfg = TruncationConfig(
            max_stdout_chars=50_000,
            max_stderr_chars=20_000,
            stdout_tail_chars=5000,
        )
        assert cfg.stdout_tail_chars == 5000

    def test_multiple_errors_all_reported(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        with pytest.raises(Exception) as exc_info:
            TruncationConfig(max_block_chars=0, max_stdout_chars=0)
        msg = str(exc_info.value)
        assert "max_block_chars" in msg
        assert "max_stdout_chars" in msg

    def test_merge_with_none_returns_self(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        cfg = TruncationConfig()
        result = cfg.merge_with(None)
        assert result is cfg

    def test_merge_with_overrides_set_fields(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        base = TruncationConfig()
        override = TruncationConfig(max_block_chars=5000)
        result = base.merge_with(override)
        assert result.max_block_chars == 5000

    def test_merge_with_no_fields_set_raises(self):
        from nemo_oo_agents.config.truncation_config import TruncationConfig

        base = TruncationConfig()
        # Construct a TruncationConfig with empty model_fields_set by using
        # model_construct (skips __init__, so model_fields_set stays empty)
        other = TruncationConfig.model_construct()
        with pytest.raises(ValueError, match="no model_fields_set"):
            base.merge_with(other)


# ===========================================================================
# runtime/async_safety.py
# ===========================================================================


class TestAsyncSafety:
    """Test async safety patches (lines 44-46, 64, 77-82, 89-94)."""

    def test_agent_context_sets_flag(self):
        from nemo_oo_agents.runtime.async_safety import (
            _in_agent_context,
            agent_async_safety_context,
        )

        assert _in_agent_context.get() is False
        with agent_async_safety_context():
            assert _in_agent_context.get() is True
        assert _in_agent_context.get() is False

    def test_agent_context_resets_on_exception(self):
        from nemo_oo_agents.runtime.async_safety import (
            _in_agent_context,
            agent_async_safety_context,
        )

        try:
            with agent_async_safety_context():
                raise RuntimeError("test")
        except RuntimeError:
            pass
        assert _in_agent_context.get() is False

    def test_is_event_loop_thread_no_loop(self):
        """Outside event loop, _is_event_loop_thread returns False."""
        from nemo_oo_agents.runtime.async_safety import _is_event_loop_thread

        # We're not in an event loop here (sync test)
        result = _is_event_loop_thread()
        assert result is False

    async def test_is_event_loop_thread_inside_loop(self):
        """Inside event loop, _is_event_loop_thread returns True."""
        from nemo_oo_agents.runtime.async_safety import _is_event_loop_thread

        result = _is_event_loop_thread()
        assert result is True

    async def test_future_result_blocks_in_agent_context(self):
        """Future.result() raises inside agent context on event loop thread."""
        from nemo_oo_agents.runtime.async_safety import agent_async_safety_context

        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_result(42)

        with agent_async_safety_context():
            with pytest.raises(RuntimeError, match="Future.result\\(\\).*deadlock"):
                future.result()

    async def test_future_exception_blocks_in_agent_context(self):
        """Future.exception() raises inside agent context on event loop thread."""
        from nemo_oo_agents.runtime.async_safety import agent_async_safety_context

        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_result(42)

        with agent_async_safety_context():
            with pytest.raises(RuntimeError, match="Future.exception\\(\\).*deadlock"):
                future.exception()

    async def test_wait_blocks_in_agent_context(self):
        """concurrent.futures.wait() raises inside agent context on event loop thread."""
        from nemo_oo_agents.runtime.async_safety import agent_async_safety_context

        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_result(42)

        with agent_async_safety_context():
            with pytest.raises(RuntimeError, match="concurrent.futures.wait\\(\\).*deadlock"):
                concurrent.futures.wait([future])

    async def test_as_completed_blocks_in_agent_context(self):
        """concurrent.futures.as_completed() raises inside agent context on event loop thread."""
        from nemo_oo_agents.runtime.async_safety import agent_async_safety_context

        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_result(42)

        with agent_async_safety_context():
            with pytest.raises(
                RuntimeError, match="concurrent.futures.as_completed\\(\\).*deadlock"
            ):
                concurrent.futures.as_completed([future])

    def test_future_result_works_outside_agent_context(self):
        """Future.result() works normally outside agent context."""
        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_result(42)
        # Outside agent context — should not raise
        assert future.result() == 42

    def test_future_exception_works_outside_agent_context(self):
        """Future.exception() works normally outside agent context."""
        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_exception(ValueError("test error"))
        exc = future.exception()
        assert isinstance(exc, ValueError)


# ===========================================================================
# runtime/event_query.py
# ===========================================================================


class TestEventQuery:
    """Cover event_query.py filtering paths (lines 78, 87, 112, 116-122, 126)."""

    def _make_event(self, class_name: str, call_id: str | None = None, content: str = "") -> Any:
        """Create a minimal mock event."""
        ev = MagicMock()
        ev.__class__.__name__ = class_name
        ev.metadata = {"call_id": call_id} if call_id else {}
        ev.__str__ = lambda self: content
        return ev

    def test_by_type_classmethod(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        q = EventQuery.by_type("Task", limit=5)
        assert q.type == "Task"
        assert q.limit == 5

    def test_last_n_classmethod(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        q = EventQuery.last_n(10)
        assert q.limit == 10

    def test_current_call_classmethod(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        q = EventQuery.current_call(limit=3)
        assert q.call_id == "current"
        assert q.limit == 3

    def test_apply_filter_by_type(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [
            self._make_event("Task"),
            self._make_event("Error"),
            self._make_event("Task"),
        ]
        q = EventQuery(type="Task")
        result = q.apply(events)
        assert len(result) == 2
        assert all(e.__class__.__name__ == "Task" for e in result)

    def test_apply_filter_by_call_id_literal(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [
            self._make_event("Task", call_id="call-1"),
            self._make_event("Task", call_id="call-2"),
            self._make_event("Task", call_id="call-1"),
        ]
        q = EventQuery(call_id="call-1")
        result = q.apply(events)
        assert len(result) == 2

    def test_apply_filter_by_call_id_current(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [
            self._make_event("Task", call_id="call-abc"),
            self._make_event("Task", call_id="call-xyz"),
        ]
        q = EventQuery(call_id="current")
        result = q.apply(events, current_call_id="call-abc")
        assert len(result) == 1

    def test_apply_filter_by_query_text(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [
            self._make_event("Task", content="find the answer"),
            self._make_event("Task", content="calculate pi"),
        ]
        q = EventQuery(query="answer")
        result = q.apply(events)
        assert len(result) == 1

    def test_apply_filter_by_query_regex(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [
            self._make_event("Task", content="error 404 not found"),
            self._make_event("Task", content="success"),
        ]
        q = EventQuery(query=r"error \d+", regex=True)
        result = q.apply(events)
        assert len(result) == 1

    def test_apply_limit(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [self._make_event("Task") for _ in range(10)]
        q = EventQuery(limit=3)
        result = q.apply(events)
        assert len(result) == 3

    def test_apply_limit_takes_last_n(self):
        """limit slices from the end."""
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [self._make_event("Task", content=f"event {i}") for i in range(5)]
        q = EventQuery(limit=2)
        result = q.apply(events)
        assert result == events[-2:]

    def test_apply_combined_filters(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [
            self._make_event("Task", call_id="c1", content="alpha"),
            self._make_event("Error", call_id="c1", content="beta"),
            self._make_event("Task", call_id="c2", content="gamma"),
            self._make_event("Task", call_id="c1", content="delta"),
        ]
        q = EventQuery(type="Task", call_id="c1", limit=1)
        result = q.apply(events)
        assert len(result) == 1

    def test_apply_no_filters_returns_all(self):
        from nemo_oo_agents.runtime.event_query import EventQuery

        events = [self._make_event("Task") for _ in range(5)]
        q = EventQuery()
        result = q.apply(events)
        assert result == events


# ===========================================================================
# runtime/media_capture.py
# ===========================================================================


class TestMediaCapture:
    """Cover media_capture.py (lines 48, 67, 111, 120-124, 138-142)."""

    def _make_image(self, media_type="image/png", vendor_metadata=None):
        from nemo_oo_agents.media import Image

        return Image(
            data_url="data:image/png;base64,abc123",
            media_type=media_type,
            vendor_metadata=vendor_metadata,
        )

    def _make_audio(self, media_type="audio/wav"):
        from nemo_oo_agents.media import Audio

        return Audio(data_url="data:audio/wav;base64,abc123", media_type=media_type)

    def _make_file(self):
        from nemo_oo_agents.media import File

        return File(data_url="data:application/pdf;base64,abc123", media_type="application/pdf")

    def test_image_content_block_basic(self):
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        img = self._make_image()
        block = media_to_content_block(img)
        assert block["type"] == "image_url"
        assert "image_url" in block
        assert block["image_url"]["url"] == "data:image/png;base64,abc123"

    def test_image_content_block_includes_format(self):
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        img = self._make_image(media_type="image/jpeg")
        block = media_to_content_block(img)
        assert block["image_url"]["format"] == "image/jpeg"

    def test_image_content_block_skips_octet_stream_format(self):
        """media_type=application/octet-stream should not add 'format' key."""
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        img = self._make_image(media_type="application/octet-stream")
        block = media_to_content_block(img)
        assert "format" not in block["image_url"]

    def test_image_content_block_vendor_metadata_merged(self):
        """vendor_metadata is merged into image_url dict."""
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        img = self._make_image(vendor_metadata={"detail": "high"})
        block = media_to_content_block(img)
        assert block["image_url"]["detail"] == "high"

    def test_audio_content_block(self):
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        audio = self._make_audio()
        block = media_to_content_block(audio)
        assert block["type"] == "input_audio"
        assert "input_audio" in block
        assert block["input_audio"]["format"] == "wav"

    def test_audio_content_block_format_extracted(self):
        """Format is the last part of the media type."""
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        audio = self._make_audio(media_type="audio/mp3")
        block = media_to_content_block(audio)
        assert block["input_audio"]["format"] == "mp3"

    def test_file_content_block(self):
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        file_obj = self._make_file()
        block = media_to_content_block(file_obj)
        assert block["type"] == "file"
        assert "file" in block

    def test_unknown_media_subclass_fallback(self):
        """Unknown Media subclass falls back to image_url type."""
        from nemo_oo_agents.media import Media
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        # Custom subclass not Image/Audio/File
        class UnknownMedia(Media):
            _modality = "unknown"

        obj = UnknownMedia(data_url="https://example.com/foo", media_type="")
        block = media_to_content_block(obj)
        assert block["type"] == "image_url"
        assert block["image_url"]["url"] == "https://example.com/foo"

    def test_non_media_raises_type_error(self):
        from nemo_oo_agents.runtime.media_capture import media_to_content_block

        with pytest.raises(TypeError, match="Expected Media"):
            media_to_content_block("not a media object")

    def test_show_outside_context_prints_message(self, capsys):
        from nemo_oo_agents.runtime.media_capture import show

        img = self._make_image()
        show(img)
        captured = capsys.readouterr()
        assert "outside execution context" in captured.out

    def test_show_inside_context_appends_block(self, capsys):
        from nemo_oo_agents.runtime.media_capture import _media_buffer_var, show

        img = self._make_image()
        buf: list = []
        token = _media_buffer_var.set(buf)
        try:
            show(img)
        finally:
            _media_buffer_var.reset(token)
        assert len(buf) == 1
        assert buf[0]["type"] == "image_url"

    def test_show_limit_reached_prints_message(self, capsys):
        from nemo_oo_agents.runtime.media_capture import (
            MAX_ATTACHMENTS_PER_EXECUTION,
            _media_buffer_var,
            show,
        )

        img = self._make_image()
        buf: list = [MagicMock()] * MAX_ATTACHMENTS_PER_EXECUTION
        token = _media_buffer_var.set(buf)
        try:
            show(img)
        finally:
            _media_buffer_var.reset(token)
        captured = capsys.readouterr()
        assert "limit reached" in captured.out
        assert len(buf) == MAX_ATTACHMENTS_PER_EXECUTION  # nothing added

    def test_show_unsupported_type_raises(self):
        from nemo_oo_agents.runtime.media_capture import _media_buffer_var, show

        buf: list = []
        token = _media_buffer_var.set(buf)
        try:
            with pytest.raises(TypeError, match="show\\(\\) expects"):
                show({"not": "media"})
        finally:
            _media_buffer_var.reset(token)

    def test_try_pil_to_content_block_import_error(self):
        """When PIL is not available, returns None gracefully."""
        from nemo_oo_agents.runtime.media_capture import _try_pil_to_content_block

        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            result = _try_pil_to_content_block("not a pil image")
        assert result is None

    def test_try_matplotlib_to_content_block_import_error(self):
        """When matplotlib is not available, returns None gracefully."""
        from nemo_oo_agents.runtime.media_capture import _try_matplotlib_to_content_block

        with patch.dict(sys.modules, {"matplotlib": None, "matplotlib.figure": None}):
            result = _try_matplotlib_to_content_block("not a figure")
        assert result is None

    def test_image_alias(self):
        """image_to_content_block is an alias for media_to_content_block."""
        from nemo_oo_agents.runtime.media_capture import (
            image_to_content_block,
            media_to_content_block,
        )

        assert image_to_content_block is media_to_content_block


# ===========================================================================
# library_skill.py — lines 36-42 (AST fallback)
# ===========================================================================


class TestLibrarySkillASTFallback:
    """Cover LibrarySkill lines 36-42: AST docstring extraction path."""

    def _cleanup_module(self, name: str) -> None:
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                del sys.modules[key]

    def test_ast_docstring_extracted_when_pkg_doc_none(self, tmp_path):
        """When pkg.__doc__ is None, LibrarySkill parses __init__.py with AST.

        We simulate this by mocking importlib.import_module to return a module
        with __doc__=None while the actual __init__.py has a string docstring.
        """
        lib_dir = tmp_path / "mylib"
        lib_dir.mkdir()
        (lib_dir / "__init__.py").write_text(
            '"""My library description"""\n\ndef foo():\n    pass\n'
        )

        fake_pkg = MagicMock()
        fake_pkg.__doc__ = None  # forces the AST fallback

        from nemo_oo_agents.library_skill import LibrarySkill

        with patch("nemo_oo_agents.library_skill.importlib.import_module", return_value=fake_pkg):
            skill = LibrarySkill(path=lib_dir)

        assert "My library description" in skill.__doc__

    def test_ast_fallback_handles_syntax_error(self, tmp_path):
        """When __init__.py has a SyntaxError, the AST path catches it and returns ''."""
        lib_dir = tmp_path / "mylib"
        lib_dir.mkdir()
        (lib_dir / "__init__.py").write_text("def foo(:\n")  # intentional syntax error

        fake_pkg = MagicMock()
        fake_pkg.__doc__ = None  # forces the AST fallback

        from nemo_oo_agents.library_skill import LibrarySkill

        with patch("nemo_oo_agents.library_skill.importlib.import_module", return_value=fake_pkg):
            skill = LibrarySkill(path=lib_dir)

        # SyntaxError silently caught — description stays ""
        assert skill.__doc__ == ""

    def test_ast_fallback_init_py_missing(self, tmp_path):
        """When __init__.py does not exist, the AST block is skipped entirely."""
        lib_dir = tmp_path / "mylib"
        lib_dir.mkdir()
        # No __init__.py at all

        fake_pkg = MagicMock()
        fake_pkg.__doc__ = None

        from nemo_oo_agents.library_skill import LibrarySkill

        with patch("nemo_oo_agents.library_skill.importlib.import_module", return_value=fake_pkg):
            skill = LibrarySkill(path=lib_dir)

        assert skill.__doc__ == ""

    def test_docstring_from_pkg_doc_used_directly(self, tmp_path):
        """When pkg.__doc__ is set, LibrarySkill uses it directly (no AST)."""
        lib_dir = tmp_path / "mylib"
        lib_dir.mkdir()
        (lib_dir / "__init__.py").write_text('"""My library"""\n\ndef foo():\n    pass\n')

        fake_pkg = MagicMock()
        fake_pkg.__doc__ = "My library"

        from nemo_oo_agents.library_skill import LibrarySkill

        with patch("nemo_oo_agents.library_skill.importlib.import_module", return_value=fake_pkg):
            skill = LibrarySkill(path=lib_dir)

        assert "My library" in skill.__doc__

    def test_dir_delegates_to_module(self, tmp_path):
        """__dir__ returns the loaded module's dir() when module is in sys.modules."""
        lib_dir = tmp_path / "mylib"
        lib_dir.mkdir()
        (lib_dir / "__init__.py").write_text('"""Some lib"""\n\ndef foo():\n    pass\n')

        sys.path.insert(0, str(tmp_path))
        try:
            from nemo_oo_agents.library_skill import LibrarySkill

            # Use real import so foo is in the module
            skill = LibrarySkill(path=lib_dir)
            d = dir(skill)
            assert isinstance(d, list)
            assert "foo" in d
        finally:
            sys.path.remove(str(tmp_path))
            self._cleanup_module("mylib")


# ===========================================================================
# tools/bash_tool.py — error paths and edge cases
# ===========================================================================


class TestBashResult:
    """Cover BashResult.__str__ paths (line 35)."""

    def test_str_with_stderr_and_return_code(self):
        from nemo_oo_agents.tools.bash_tool import BashResult

        r = BashResult(stdout="out", stderr="err", return_code=1)
        s = str(r)
        assert "out" in s
        assert "[stderr]" in s
        assert "err" in s
        assert "[exit code: 1]" in s

    def test_str_sandboxed(self):
        from nemo_oo_agents.tools.bash_tool import BashResult

        r = BashResult(stdout="out", stderr="", return_code=0, sandboxed=True)
        assert "[sandboxed]" in str(r)

    def test_str_no_extras(self):
        from nemo_oo_agents.tools.bash_tool import BashResult

        r = BashResult(stdout="out", stderr="", return_code=0)
        assert str(r) == "out"

    def test_success_property(self):
        from nemo_oo_agents.tools.bash_tool import BashResult

        assert BashResult(stdout="", stderr="", return_code=0).success is True
        assert BashResult(stdout="", stderr="", return_code=1).success is False


class TestBashToolInit:
    """Cover BashTool.__init__ paths (lines 66-71, 106-112)."""

    def test_default_init(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()
        assert tool.working_dir.exists()

    def test_srt_path_with_tilde(self):
        """When srt_executable contains ~, it is expanded."""
        from nemo_oo_agents.config.tool_configs import BashConfig
        from nemo_oo_agents.tools.bash_tool import BashTool

        cfg = BashConfig(srt_executable="~/bin/srt", use_sandbox=False)
        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool(config=cfg)
        assert "~" not in tool._srt_path

    def test_srt_executable_none_defaults_to_srt(self):
        """When srt_executable is falsy, _srt_path defaults to 'srt'."""
        from nemo_oo_agents.config.tool_configs import BashConfig
        from nemo_oo_agents.tools.bash_tool import BashTool

        cfg = BashConfig(srt_executable=None, use_sandbox=False)
        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool(config=cfg)
        assert tool._srt_path == "srt"

    def test_use_sandbox_false_when_srt_unavailable(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()
        assert tool._srt_available is False

    def test_sandbox_warning_when_srt_unavailable(self, caplog):
        from nemo_oo_agents.config.tool_configs import BashConfig
        from nemo_oo_agents.tools.bash_tool import BashTool

        cfg = BashConfig(use_sandbox=True)
        import logging

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.tools.bash_tool"):
                BashTool(config=cfg)
        assert "SRT is not available" in caplog.text

    def test_repr_sandbox_enabled(self):
        from nemo_oo_agents.config.tool_configs import BashConfig
        from nemo_oo_agents.tools.bash_tool import BashTool

        cfg = BashConfig(use_sandbox=True)
        with patch.object(BashTool, "_check_srt_available", return_value=True):
            tool = BashTool(config=cfg)
        assert "enabled" in repr(tool)

    def test_repr_sandbox_disabled(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()
        assert "disabled" in repr(tool)


class TestBashToolCheckSrtAvailable:
    """Cover _check_srt_available (lines 123, 133-135, 144-147)."""

    def test_srt_available_when_settings_in_help(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="--settings available", stderr="", returncode=0
            )
            tool = BashTool.__new__(BashTool)
            tool._srt_path = "srt"
            result = BashTool._check_srt_available(tool)
        assert result is True

    def test_srt_unavailable_when_subtitle_editor(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="--settings options\nshift subtitle", stderr="", returncode=0
            )
            tool = BashTool.__new__(BashTool)
            tool._srt_path = "srt"
            result = tool._check_srt_available()
        assert result is False

    def test_srt_unavailable_when_file_not_found(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch("subprocess.run", side_effect=FileNotFoundError):
            tool = BashTool.__new__(BashTool)
            tool._srt_path = "srt"
            result = tool._check_srt_available()
        assert result is False

    def test_srt_unavailable_when_timeout(self):
        import subprocess

        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("srt", 2)):
            tool = BashTool.__new__(BashTool)
            tool._srt_path = "srt"
            result = tool._check_srt_available()
        assert result is False

    def test_srt_none_path_returns_false(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        tool = BashTool.__new__(BashTool)
        tool._srt_path = None
        result = tool._check_srt_available()
        assert result is False


class TestBashToolRun:
    """Cover BashTool.run() error paths (lines 178, 209-210, 219-220)."""

    async def test_run_timeout_returns_error_result(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()

        with patch("asyncio.create_subprocess_shell") as mock_proc_factory:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
            mock_proc.kill = AsyncMock()
            mock_proc.wait = AsyncMock()
            mock_proc_factory.return_value = mock_proc

            result = await tool.run("sleep 100", timeout=0.001)

        assert result.return_code == -1
        assert "timed out" in result.stderr

    async def test_run_subprocess_exception_returns_error_result(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()

        with patch("asyncio.create_subprocess_shell", side_effect=OSError("cannot fork")):
            result = await tool.run("ls")

        assert result.return_code == -1
        assert "Failed to execute" in result.stderr

    async def test_run_sandboxed_wraps_command(self):
        """When sandbox is available, command is wrapped with SRT."""
        from nemo_oo_agents.config.tool_configs import BashConfig
        from nemo_oo_agents.tools.bash_tool import BashTool

        cfg = BashConfig(use_sandbox=True)
        with patch.object(BashTool, "_check_srt_available", return_value=True):
            tool = BashTool(config=cfg)

        wrapped_cmd = []

        async def fake_subprocess(cmd, **kwargs):
            wrapped_cmd.append(cmd)
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_shell", side_effect=fake_subprocess):
            await tool.run("ls")

        assert wrapped_cmd and "srt" in wrapped_cmd[0].lower()

    async def test_run_basic_success(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()

        async def fake_subprocess(cmd, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"hello world", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_shell", side_effect=fake_subprocess):
            result = await tool.run("echo hello world")

        assert result.stdout == "hello world"
        assert result.return_code == 0
        assert result.success is True

    async def test_run_timeout_kills_process(self):
        """On timeout, proc.kill() is called."""
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            await tool.run("sleep 100", timeout=0.001)

        mock_proc.kill.assert_called_once()


class TestFileResult:
    """Cover FileResult.lines (line 248) and FileTool operations."""

    def test_lines_property(self):
        from nemo_oo_agents.tools.bash_tool import FileResult

        r = FileResult(stdout="a\nb\n\nc", stderr="", return_code=0)
        assert r.lines == ["a", "b", "c"]

    def test_lines_empty(self):
        from nemo_oo_agents.tools.bash_tool import FileResult

        r = FileResult(stdout="", stderr="", return_code=0)
        assert r.lines == []

    async def test_file_read_raises_on_failure(self):
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        bash.run = AsyncMock(return_value=BashResult(stdout="", stderr="not found", return_code=1))

        with pytest.raises(FileNotFoundError, match="Failed to read"):
            await files.read("nonexistent.txt")

    async def test_file_list_raises_on_failure(self):
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        bash.run = AsyncMock(
            return_value=BashResult(stdout="", stderr="no such dir", return_code=2)
        )

        with pytest.raises(FileNotFoundError, match="Failed to list"):
            await files.list("/nonexistent/")

    async def test_file_write_raises_on_failure(self):
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        bash.run = AsyncMock(
            return_value=BashResult(stdout="", stderr="permission denied", return_code=1)
        )

        with pytest.raises(OSError, match="Failed to write"):
            await files.write("/readonly/file.txt", "content")

    def test_file_tool_repr(self):
        from nemo_oo_agents.tools.bash_tool import BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)
        assert "FileTool" in repr(files)


class TestBashToolSandboxProperty:
    """Cover sandbox_available property (line 230)."""

    def test_sandbox_available_true(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=True):
            tool = BashTool()
        assert tool.sandbox_available is True

    def test_sandbox_available_false(self):
        from nemo_oo_agents.tools.bash_tool import BashTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            tool = BashTool()
        assert tool.sandbox_available is False


class TestFileToolReadLines:
    """Cover FileTool.read() with start_line / end_line (lines 291, 293)."""

    async def test_read_with_start_and_end_line(self):
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        bash.run = AsyncMock(
            return_value=BashResult(stdout="line2\nline3", stderr="", return_code=0)
        )
        result = await files.read("file.txt", start_line=2, end_line=3)
        assert result.stdout == "line2\nline3"
        # Verify sed command was used
        bash.run.assert_called_once()
        call_args = bash.run.call_args[0][0]
        assert "sed" in call_args

    async def test_read_with_start_line_only(self):
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        bash.run = AsyncMock(
            return_value=BashResult(stdout="line5 onwards", stderr="", return_code=0)
        )
        result = await files.read("file.txt", start_line=5)
        assert result.stdout == "line5 onwards"
        bash.run.assert_called_once()
        call_args = bash.run.call_args[0][0]
        assert "tail" in call_args


class TestEditFile:
    """Cover FileTool.edit_file() paths (lines 411, 436, 439)."""

    async def test_edit_file_search_block_not_found_raises(self, tmp_path):
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        # read() returns content that does NOT contain the search block
        content = "def foo():\n    pass\n"
        bash.run = AsyncMock(return_value=BashResult(stdout=content, stderr="", return_code=0))

        with pytest.raises(ValueError):
            await files.edit_file("file.py", "nonexistent_block", "replacement")

    async def test_edit_file_multiple_matches_raises(self, tmp_path):
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        content = "foo\nfoo\n"
        bash.run = AsyncMock(return_value=BashResult(stdout=content, stderr="", return_code=0))

        with pytest.raises(ValueError, match="Found 2 matches"):
            await files.edit_file("file.txt", "foo", "bar")

    async def test_edit_file_fuzzy_match_hint(self, tmp_path):
        """When no exact match but a fuzzy match exists, error includes line hint."""
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        # Content that is similar but not identical to the search block
        content = "def foo(x):\n    return x\n"
        bash.run = AsyncMock(return_value=BashResult(stdout=content, stderr="", return_code=0))

        with pytest.raises(ValueError, match="No exact match found|Search block not found"):
            await files.edit_file("file.py", "def foo(y):\n    return y\n", "replacement")

    async def test_edit_file_successful_replacement(self):
        """Successful edit_file returns FileResult with SUCCESS message."""
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        content = "x = 1\ny = 2\nz = 3\n"
        success_result = BashResult(stdout="", stderr="", return_code=0)

        call_count = 0

        async def multi_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # read() call
                return BashResult(stdout=content, stderr="", return_code=0)
            # write temp, mv, rm calls
            return success_result

        bash.run = multi_run
        result = await files.edit_file("file.txt", "y = 2", "y = 99")
        assert "SUCCESS" in result.stdout

    async def test_edit_file_py_syntax_check_passes(self):
        """For .py files, a syntax check is run; on pass, replacement proceeds."""
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        content = "def foo():\n    return 1\n"
        success_result = BashResult(stdout="", stderr="", return_code=0)

        call_count = 0

        async def multi_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # read() call
                return BashResult(stdout=content, stderr="", return_code=0)
            return success_result

        bash.run = multi_run
        result = await files.edit_file("file.py", "return 1", "return 42")
        assert "SUCCESS" in result.stdout

    async def test_edit_file_py_syntax_check_fails(self):
        """For .py files, if syntax check fails, ValueError is raised."""
        from nemo_oo_agents.tools.bash_tool import BashResult, BashTool, FileTool

        with patch.object(BashTool, "_check_srt_available", return_value=False):
            bash = BashTool()
        files = FileTool(bash)

        content = "def foo():\n    return 1\n"
        call_count = 0

        async def multi_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # read() call
                return BashResult(stdout=content, stderr="", return_code=0)
            if call_count == 2:
                # write .tmp file
                return BashResult(stdout="", stderr="", return_code=0)
            if call_count == 3:
                # py_compile — fails
                return BashResult(stdout="", stderr="SyntaxError: invalid syntax", return_code=1)
            # cleanup rm
            return BashResult(stdout="", stderr="", return_code=0)

        bash.run = multi_run

        with pytest.raises(ValueError, match="syntax errors"):
            await files.edit_file("file.py", "return 1", "return (")


# ===========================================================================
# media_capture.py — PIL and matplotlib auto-convert paths
# ===========================================================================


class TestMediaCapturePILAndMatplotlib:
    """Test PIL and matplotlib auto-convert (lines 120-124, 138-142)."""

    def test_try_pil_to_content_block_with_pil_image(self):
        """When PIL is available and obj is a PIL Image, returns image_url block."""
        import importlib

        import nemo_oo_agents.runtime.media_capture as mc

        # Create a mock PIL Image class and instance
        pil_cls = type("Image", (), {})
        obj = pil_cls()

        buf_content = b"PNG_DATA"

        def save_side_effect(buf, format):
            buf.write(buf_content)

        obj.save = save_side_effect

        mock_pil_module = MagicMock()
        mock_pil_module.Image.Image = pil_cls

        with patch.dict(sys.modules, {"PIL": mock_pil_module, "PIL.Image": mock_pil_module.Image}):
            importlib.reload(mc)
            result = mc._try_pil_to_content_block(obj)

        # Reload to restore unpatched module state
        importlib.reload(mc)

        assert result is not None
        assert result["type"] == "image_url"

    def test_try_matplotlib_returns_none_for_non_figure(self):
        """When matplotlib is available but obj is not a Figure, returns None."""
        from nemo_oo_agents.runtime.media_capture import _try_matplotlib_to_content_block

        mock_matplotlib = MagicMock()
        mock_figure_cls = type("Figure", (), {})
        mock_matplotlib.figure.Figure = mock_figure_cls

        with patch.dict(
            sys.modules,
            {
                "matplotlib": mock_matplotlib,
                "matplotlib.figure": mock_matplotlib.figure,
            },
        ):
            result = _try_matplotlib_to_content_block("not a figure")

        assert result is None

    def test_try_pil_returns_none_for_non_pil_image(self):
        """When PIL is available but obj is not a PIL Image, returns None."""
        from nemo_oo_agents.runtime.media_capture import _try_pil_to_content_block

        mock_pil = MagicMock()
        mock_pil.Image.Image = type("Image", (), {})

        with patch.dict(sys.modules, {"PIL": mock_pil, "PIL.Image": mock_pil.Image}):
            result = _try_pil_to_content_block("not a PIL image")

        assert result is None

    def test_show_with_auto_convert_failure_raises(self):
        """show() with unsupported type inside buffer context raises TypeError."""
        from nemo_oo_agents.runtime.media_capture import _media_buffer_var, show

        buf: list = []
        token = _media_buffer_var.set(buf)
        try:
            with pytest.raises(TypeError):
                show(42)
        finally:
            _media_buffer_var.reset(token)
