"""Failing test: recovery path doesn't archive when prompt_tokens can't be parsed.

Replicates the bug found in e2e testing: when the API error says
"your request has N input tokens" (not "prompt contains N tokens"),
_parse_prompt_tokens returns None -> _compute_reduced_max_tokens returns None
-> the error is re-raised WITHOUT calling _archive_on_context_error.

Two bugs:
1. _parse_prompt_tokens regex only matches "prompt ... N tokens", not "request has N input tokens"
2. _archive_on_context_error is called AFTER `if _reduced is None: raise`, so it never fires
   when the prompt token count can't be parsed.
"""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.events import Message
from nemo_oo_agents.runtime.actor import (
    _current_llm_var,
    _current_method_var,
    _parse_prompt_tokens,
)
from nemo_oo_agents.unifiedllm import FakeLLMClient


class _ContextWindowExceededError(Exception):
    """Simulates litellm.BadRequestError wrapping ContextWindowExceededError."""
    pass


class _FakeLLM(FakeLLMClient):
    _cw = 262_144
    @property
    def context_window(self):
        return self._cw


def _mk_llm(context_window=262_144):
    class _LLM(_FakeLLM):
        _cw = context_window
    llm = _LLM()
    llm.model = "gpt-4o"
    return llm


class TestPromptTokensRegex:
    """_parse_prompt_tokens must handle all common API error formats."""

    def test_openai_format(self):
        """OpenAI: 'prompt contains at least 67073 tokens'."""
        exc = Exception(
            "This model's maximum context length is 131072 tokens. "
            "However, you requested 64000 output tokens and your prompt "
            "contains at least 67073 input tokens."
        )
        assert _parse_prompt_tokens(exc) == 67073

    def test_nvidia_gateway_format(self):
        """NVIDIA gateway: 'your request has 1203158 input tokens'."""
        exc = Exception(
            "This model's maximum context length is 262144 tokens. "
            "However, your request has 1203158 input tokens. "
            "Please reduce the length of the input messages."
        )
        result = _parse_prompt_tokens(exc)
        assert result == 1203158, (
            f"Should extract 1203158 from NVIDIA gateway format, got {result}"
        )

    def test_litellm_wrapped_nvidia_format(self):
        """litellm wraps: 'ContextWindowExceededError: ... request has N input tokens'."""
        exc = Exception(
            "litellm.BadRequestError: OpenAIException - "
            "litellm.ContextWindowExceededError: "
            "This model's maximum context length is 262144 tokens. "
            "However, your request has 1203158 input tokens."
        )
        result = _parse_prompt_tokens(exc)
        assert result == 1203158, (
            f"Should handle litellm-wrapped NVIDIA format, got {result}"
        )


class TestArchivalFiresOnContextError:
    """_archive_on_context_error must fire even when prompt tokens can't be parsed."""

    @pytest.mark.asyncio
    async def test_archival_fires_when_reduced_is_none(self):
        """When _compute_reduced_max_tokens returns None (can't parse prompt tokens
        and no max_tokens provided), archival should STILL fire before re-raising.

        Currently broken: archival is called AFTER `if _reduced is None: raise`.
        """
        from unittest.mock import patch

        llm = _mk_llm(262_144)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for i in range(20):
            agent.event_manager.add(Message(content=f"message {i} " * 50))

        n_events_before = len(list(agent.event_manager.keys()))

        error = _ContextWindowExceededError(
            "This model's maximum context length is 262144 tokens. "
            "However, your request has 500000 input tokens."
        )

        call_count = 0
        original_acall = llm.acall

        async def mock_acall(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise error
            return await original_acall(messages, **kw)

        summary_events = []
        agent.event_manager.on("Summary", lambda ev: summary_events.append(ev))

        method = type(agent).respond
        llm_token = _current_llm_var.set(llm)
        method_token = _current_method_var.set(method)
        try:
            with patch.object(llm, "acall", side_effect=mock_acall):
                with patch(
                    "nemo_oo_agents.runtime.actor._is_context_window_error",
                    side_effect=lambda exc: isinstance(exc, _ContextWindowExceededError),
                ):
                    response, event_id = await agent.runtime.generate(
                        tools=[], max_tokens=None
                    )
        finally:
            _current_llm_var.reset(llm_token)
            _current_method_var.reset(method_token)

        # Archival should have fired even though max_tokens=None
        n_events_after = len(list(agent.event_manager.keys()))
        assert n_events_after < n_events_before, (
            f"Archival should reduce events: {n_events_after} >= {n_events_before}. "
            f"Summary events: {len(summary_events)}"
        )
        assert len(summary_events) >= 1, "Archival should emit Summary events"
