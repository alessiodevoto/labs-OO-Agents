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
        assert result == 1203158, f"Should extract 1203158 from NVIDIA gateway format, got {result}"

    def test_litellm_wrapped_nvidia_format(self):
        """litellm wraps: 'ContextWindowExceededError: ... request has N input tokens'."""
        exc = Exception(
            "litellm.BadRequestError: OpenAIException - "
            "litellm.ContextWindowExceededError: "
            "This model's maximum context length is 262144 tokens. "
            "However, your request has 1203158 input tokens."
        )
        result = _parse_prompt_tokens(exc)
        assert result == 1203158, f"Should handle litellm-wrapped NVIDIA format, got {result}"


class TestArchivalFiresOnContextError:
    """_archive_on_context_error must fire even when prompt tokens can't be parsed."""

    @pytest.mark.asyncio
    async def test_archival_fires_when_reduced_is_none(self):
        """When _compute_reduced_max_tokens returns None (can't parse prompt tokens
        and no max_tokens provided), archival should STILL fire before re-raising.

        generate() archives events then re-raises (since it can't compute a
        reduced max_tokens). The caller (e.g. CodeAct) retries with fresh
        messages built from the now-smaller event store.
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

        summary_events = []
        agent.event_manager.on("Summary", lambda ev: summary_events.append(ev))

        method = type(agent).respond
        llm_token = _current_llm_var.set(llm)
        method_token = _current_method_var.set(method)
        try:
            with patch.object(llm, "acall", side_effect=error):
                with patch(
                    "nemo_oo_agents.runtime.actor._is_context_window_error",
                    side_effect=lambda exc: isinstance(exc, _ContextWindowExceededError),
                ):
                    with pytest.raises(_ContextWindowExceededError):
                        await agent.runtime.generate(tools=[], max_tokens=None)
        finally:
            _current_llm_var.reset(llm_token)
            _current_method_var.reset(method_token)

        # Archival should have fired BEFORE the re-raise
        n_events_after = len(list(agent.event_manager.keys()))
        assert n_events_after < n_events_before, (
            f"Archival should reduce events: {n_events_after} >= {n_events_before}. "
            f"Summary events: {len(summary_events)}"
        )
        assert len(summary_events) >= 1, "Archival should emit Summary events"

    @pytest.mark.asyncio
    async def test_archival_fires_with_unparseable_token_count_and_known_ratio(self):
        """When the error message has no recognizable token count but a
        calibration ratio was already learned from a prior successful call,
        archival still fires using the known ratio.

        In practice, the ratio is almost always available by the time
        context overflows — it's learned from the very first successful call.
        """
        from unittest.mock import patch

        llm = _mk_llm(4_096)  # tiny window so small events overflow

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        for i in range(20):
            agent.event_manager.add(Message(content=f"message {i} " * 50))

        n_events_before = len(list(agent.event_manager.keys()))

        # Simulate a prior successful call having set the calibration ratio
        agent.runtime._token_calibration_ratio = 1.5

        # Error with NO parseable token count — some unknown provider format
        error = _ContextWindowExceededError("context length exceeded: too many tokens in the input")

        summary_events = []
        agent.event_manager.on("Summary", lambda ev: summary_events.append(ev))

        method = type(agent).respond
        llm_token = _current_llm_var.set(llm)
        method_token = _current_method_var.set(method)
        try:
            with patch.object(llm, "acall", side_effect=error):
                with patch(
                    "nemo_oo_agents.runtime.actor._is_context_window_error",
                    side_effect=lambda exc: isinstance(exc, _ContextWindowExceededError),
                ):
                    with pytest.raises(_ContextWindowExceededError):
                        await agent.runtime.generate(tools=[], max_tokens=None)
        finally:
            _current_llm_var.reset(llm_token)
            _current_method_var.reset(method_token)

        # _parse_prompt_tokens should return None for this error
        assert _parse_prompt_tokens(error) is None, "This error format should NOT be parseable"

        # Archival should fire using the pre-existing ratio
        n_events_after = len(list(agent.event_manager.keys()))
        assert n_events_after < n_events_before, (
            f"Archival should fire with known ratio even when token count unparseable: "
            f"{n_events_after} >= {n_events_before}. "
            f"Summary events: {len(summary_events)}"
        )
        assert len(summary_events) >= 1, "Archival should emit Summary events"


class TestContextWindowErrorDetection:
    """Provider context-window errors must be recognized for fallback archival."""

    def test_azure_context_length_exceeded_format(self):
        from nemo_oo_agents.runtime.actor import _is_context_window_error

        exc = Exception(
            "litellm.BadRequestError: AzureException BadRequestError - "
            "{\n  \"error\": {\n"
            "    \"message\": \"Your input exceeds the context window of this model. "
            "Please adjust your input and try again.\",\n"
            "    \"code\": \"context_length_exceeded\"\n"
            "  }\n}"
        )

        assert _is_context_window_error(exc)
