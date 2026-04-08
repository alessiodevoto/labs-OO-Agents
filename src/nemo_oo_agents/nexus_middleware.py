"""NAT Nexus integration via the ``intercept()`` middleware API.

When ``nat_nexus`` is installed, this module provides two middleware functions
that route LLM calls and code execution through the Nexus pipeline (guardrails,
intercepts, event subscribers, ATIF export).

Usage::

    from nemo_oo_agents.nexus_middleware import install_nexus

    # Inside an async context where nat_nexus scope is active:
    uninstall = install_nexus(agent.event_manager)
    try:
        result = await agent.my_method()
    finally:
        uninstall()

Or use ``nexus_scope()`` which handles install/uninstall automatically::

    from nemo_oo_agents.nexus_middleware import nexus_scope

    async with nexus_scope(agent, "my-agent"):
        result = await agent.my_method()

Requirements:
    ``nat_nexus`` must be installed (``pip install nvidia-nat-nexus``).
    If not installed, ``install_nexus()`` and ``nexus_scope()`` raise
    ``ImportError`` with install instructions.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from nemo_oo_agents.runtime.middleware import (
    MIDDLEWARE_AGENT_CALL,
    MIDDLEWARE_EXECUTE_PYTHON,
    MIDDLEWARE_LLM_CALL,
)

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nemo_oo_agents.runtime.event_manager import EventManager
    from nemo_oo_agents.runtime.middleware import (
        AgentCallContext,
        AgentCallNext,
        ExecutePythonContext,
        ExecutePythonNext,
        LLMCallContext,
        LLMCallNext,
    )

try:
    import nat_nexus  # type: ignore[import]
    from nat_nexus import LLMRequest  # type: ignore[import]

    _HAS_NAT_NEXUS = True
except ImportError:
    _HAS_NAT_NEXUS = False
    nat_nexus = None  # type: ignore[assignment]
    LLMRequest = None  # type: ignore[assignment,misc]

_INSTALL_MSG = "nat_nexus is required for Nexus integration. Install with: uv sync --extra nexus"

# Keys stripped from params before exposing to Nexus guardrails/events.
_SENSITIVE_KEYS: frozenset[str] = frozenset({"api_key", "api_base", "base_url"})

# Keys that hold non-JSON-serializable objects (Tool instances, Pydantic models).
# These are converted or removed before constructing LLMRequest.
_NON_SERIALIZABLE_KEYS: frozenset[str] = frozenset({"tools", "output_model"})


# ---------------------------------------------------------------------------
# Middleware functions
# ---------------------------------------------------------------------------


async def nexus_llm_middleware(
    ctx: LLMCallContext,
    nxt: LLMCallNext,
) -> LLMCallContext:
    """Route LLM calls through the Nexus LLM pipeline.

    Strips sensitive keys, wraps the call through ``nat_nexus.llm.execute()``,
    and returns the original ``LLMResponse`` to the caller.  The JSON-serialized
    response is what flows through Nexus guardrails and ATIF export.
    """
    assert nat_nexus is not None

    # Get model name from the agent's LLM client (not in params).
    model_name = ""
    if ctx.agent is not None:
        llm = getattr(ctx.agent, "_llm", None)
        if llm is not None:
            model_name = getattr(llm, "model", "")
    safe_params = {
        k: v
        for k, v in ctx.params.items()
        if k not in _SENSITIVE_KEYS and k not in _NON_SERIALIZABLE_KEYS
    }
    safe_params["messages"] = ctx.messages
    # Tools are excluded via _NON_SERIALIZABLE_KEYS.  Do NOT re-add them:
    # including a "tools" key in request.content triggers an AttributeError
    # ('dict' object has no attribute 'name') inside Nexus's native pipeline.
    # The old hooks-based integration avoided this because it intercepted at
    # unifiedllm's layer, where api_params never contained a tools key.
    request = LLMRequest({}, safe_params)  # type: ignore[misc]

    captured_ctx: LLMCallContext | None = None

    # Keys from LLMRequest.content that map directly to ctx.params.
    # If a Nexus request intercept modifies these, we propagate them.
    _PROPAGATABLE_LLM_PARAMS: set[str] = {
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "tools",
    }

    async def _wrapper(req: Any) -> Any:
        nonlocal captured_ctx
        # Apply request intercept modifications back to ctx.
        # Nexus request intercepts can transform the LLMRequest (e.g. inject
        # system messages, modify headers).  The modified request is passed
        # here as `req`; we must propagate those changes to ctx so the rest
        # of the nemo_oo_agents middleware chain (and the actual LLM call) sees them.
        if hasattr(req, "content") and isinstance(req.content, dict):
            intercepted = req.content
            intercepted_msgs = intercepted.get("messages")
            if intercepted_msgs is not None:
                ctx.messages = intercepted_msgs
            # Propagate any supported param changes from the intercept.
            for key in _PROPAGATABLE_LLM_PARAMS:
                if key in intercepted:
                    ctx.params[key] = intercepted[key]
        # Call the rest of the middleware chain → eventually hits acall()
        captured_ctx = await nxt(ctx)
        # Return JSON to Nexus (guardrails, events, ATIF).
        resp = captured_ctx.response
        if resp is None:
            return {}
        # Prefer the raw litellm ModelResponse (Pydantic) — gives Nexus the
        # full OpenAI-style structure matching what the old hooks-based
        # integration returned via captured_response.model_dump(mode="json").
        raw = getattr(resp, "raw_response", None)
        if raw is not None and hasattr(raw, "model_dump"):
            return raw.model_dump(mode="json")
        # Pydantic response (e.g. passed directly)
        if hasattr(resp, "model_dump"):
            return resp.model_dump(mode="json")  # type: ignore[union-attr]
        # Fallback: manual serialization from unifiedllm.LLMResponse dataclass.
        if hasattr(resp, "assistant_message"):
            result: dict[str, Any] = {"message": resp.assistant_message}
            if resp.usage:
                result["usage"] = resp.usage
            if resp.finish_reason:
                result["finish_reason"] = resp.finish_reason
            return result
        return {}

    # Note: nat_nexus.llm.execute() returns the pre-guardrail response.
    # Sanitize-response guardrails transform data for Nexus internals
    # (ATIF export, event subscribers) but the caller always receives
    # the original response.  Conditional-execution guardrails that
    # reject raise GuardrailRejected, which propagates naturally.
    await nat_nexus.llm.execute(model_name, request, _wrapper, model_name=model_name)  # type: ignore[union-attr]

    if captured_ctx is not None:
        return captured_ctx
    # Nexus guardrails blocked the LLM call (never invoked _wrapper).
    # Raise an explicit error instead of returning ctx without a response,
    # which would trigger a confusing generic RuntimeError downstream.
    raise RuntimeError(
        "Nexus guardrail blocked the LLM call — the request was rejected "
        "before reaching the LLM. Check your Nexus guardrail configuration."
    )


async def nexus_tool_middleware(
    ctx: ExecutePythonContext,
    nxt: ExecutePythonNext,
) -> ExecutePythonContext:
    """Route code execution through the Nexus tool pipeline.

    Extracts the meaningful return value from ``ExecutionResult``, serializes
    it via ``BestEffortAnyCodec`` for Nexus inspection, and returns the
    original ``ExecutionResult`` to the caller.
    """
    assert nat_nexus is not None

    args = {
        "code": ctx.code,
        **{k: v for k, v in ctx.params.items() if k in ("tool_call_id", "timeout")},
    }
    codec = nat_nexus.typed.BestEffortAnyCodec()  # type: ignore[union-attr]

    captured_ctx: ExecutePythonContext | None = None

    # Tool execution args that Nexus intercepts may modify.
    _PROPAGATABLE_TOOL_PARAMS: set[str] = {"tool_call_id", "timeout"}

    async def _wrapper(inner_args: Any) -> Any:
        nonlocal captured_ctx
        # Apply request intercept modifications back to ctx.
        # Nexus tool request intercepts can transform args (e.g. rewrite code,
        # modify timeout).  The modified args are passed here as `inner_args`;
        # we must propagate changes to ctx so the actual execution sees them.
        if isinstance(inner_args, dict):
            if "code" in inner_args:
                ctx.code = inner_args["code"]
            for key in _PROPAGATABLE_TOOL_PARAMS:
                if key in inner_args:
                    ctx.params[key] = inner_args[key]
        # Call the rest of the middleware chain → eventually executes code
        captured_ctx = await nxt(ctx)
        result = captured_ctx.result
        if result is None:
            return codec.to_json(None)

        # Extract meaningful return value (same priority as original _nat_nexus.py)
        from nemo_oo_agents.events import _NO_RETURN

        rv = getattr(result, "returned_value", _NO_RETURN)
        if rv is _NO_RETURN:
            sig = getattr(result, "signal", None)
            if sig is not None:
                sig_data = getattr(sig, "result", None)
                if isinstance(sig_data, dict) and "result" in sig_data:
                    rv = sig_data["result"]
                else:
                    rv = None
            else:
                rv = getattr(result, "stdout", None) or None

        return codec.to_json(rv)

    # Note: nat_nexus.tools.execute() returns the pre-guardrail result.
    # Sanitize-response guardrails transform data for Nexus internals
    # (ATIF export, event subscribers) but the caller always receives
    # the original result.  Conditional-execution guardrails that reject
    # raise GuardrailRejected, which propagates naturally.
    await nat_nexus.tools.execute("execute_python", args, _wrapper)  # type: ignore[union-attr]

    if captured_ctx is not None:
        return captured_ctx
    # Nexus guardrails blocked code execution (never invoked _wrapper).
    raise RuntimeError(
        "Nexus guardrail blocked code execution — the request was rejected "
        "before running. Check your Nexus guardrail configuration."
    )


async def nexus_agent_call_middleware(
    ctx: AgentCallContext,
    nxt: AgentCallNext,
) -> AgentCallContext:
    """Wrap each agent method call in a Nexus Function scope.

    Pushes a ``ScopeType.Function`` scope named ``"AgentClass.method_name"``
    before the method executes and pops it after, giving ATIF per-method
    granularity.
    """
    assert nat_nexus is not None

    scope_name = f"{type(ctx.agent).__name__}.{ctx.method_name}"
    handle = nat_nexus.scope.push(scope_name, nat_nexus.ScopeType.Function)  # type: ignore[union-attr]
    try:
        return await nxt(ctx)
    finally:
        try:
            nat_nexus.scope.pop(handle)  # type: ignore[union-attr]
        except Exception:
            _logger.debug("nexus_agent_call_middleware: scope.pop() failed", exc_info=True)


# ---------------------------------------------------------------------------
# Install / uninstall helpers
# ---------------------------------------------------------------------------


def install_nexus(event_manager: EventManager) -> Callable[[], None]:
    """Register Nexus middleware on an event manager.

    Returns an uninstall function that removes both middleware.

    Raises:
        ImportError: If ``nat_nexus`` is not installed.
    """
    if not _HAS_NAT_NEXUS:
        raise ImportError(_INSTALL_MSG)

    unsub_agent = event_manager.intercept(MIDDLEWARE_AGENT_CALL, nexus_agent_call_middleware)
    unsub_llm = event_manager.intercept(MIDDLEWARE_LLM_CALL, nexus_llm_middleware)
    unsub_exec = event_manager.intercept(MIDDLEWARE_EXECUTE_PYTHON, nexus_tool_middleware)

    def uninstall() -> None:
        unsub_agent()
        unsub_llm()
        unsub_exec()

    return uninstall


@asynccontextmanager
async def nexus_scope(
    agent: Any,
    scope_name: str,
) -> AsyncIterator[Any]:
    """Async context manager that activates Nexus for an agent.

    Pushes a Nexus scope, installs middleware on the agent's event manager,
    and cleans up on exit::

        async with nexus_scope(agent, "research-agent") as handle:
            result = await agent.research("quantum computing")
            # handle.uuid available for ATIF export

    Args:
        agent: The Agent instance whose event_manager will get middleware.
        scope_name: Human-readable name for the Nexus scope.

    Yields:
        The Nexus scope handle (has ``.uuid`` for ATIF correlation).

    Raises:
        ImportError: If ``nat_nexus`` is not installed.
    """
    if not _HAS_NAT_NEXUS:
        raise ImportError(_INSTALL_MSG)

    uninstall = install_nexus(agent.event_manager)
    try:
        with nat_nexus.scope.scope(  # type: ignore[union-attr]
            scope_name,
            nat_nexus.ScopeType.Agent,  # type: ignore[union-attr]
        ) as handle:
            yield handle
    finally:
        uninstall()
