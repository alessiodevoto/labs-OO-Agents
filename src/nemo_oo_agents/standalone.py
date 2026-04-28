# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Standalone generation functions — LLM-powered functions with no agent state.

A standalone generation function is an async function decorated with ``@strategy``
that has no ``self`` parameter.  Each call gets a fresh agent stub (no shared state,
history disappears after the call).  Context blocks can be supplied via the decorator:

    @strategy(CodeActStrategy(), ScopedContext(context={"role": "expert"}), llm=llm)
    async def summarise(text: str) -> str:
        \"\"\"Summarise {text} in one sentence.\"\"\"
        ...

    result = await summarise("long article...")

Design constraints:
- No doc(self) / system_prompt — framework_blocks is empty.
- No persistent state — EventManager and ContextManager are fresh each invocation.
- History disappears — each call uses its own agent instance.
- Full strategy support — same CodeActStrategy / PredictStrategy machinery as agents.
- exec_globals correctness — the stub agent class is rooted in the function's own
  module so ``filter_module_globals`` exposes the caller's types to generated code.
"""

import inspect
import types
from collections.abc import Callable
from functools import wraps
from typing import Any

# Cache of per-module _StandaloneAgent subclasses, keyed by module __name__.
# Each subclass has __module__ set to the function's module so that
# ActorRuntime.execute_code's filter_module_globals picks up the right symbols.
_agent_cls_cache: dict[str, type] = {}


def _get_agent_cls(module_name: str) -> type:
    """Return a _StandaloneAgent subclass whose ``__module__`` is *module_name*.

    The class is cached so we never create more than one per module.
    """
    if module_name in _agent_cls_cache:
        return _agent_cls_cache[module_name]

    from context_blocks.render_config import RenderConfig
    from nemo_oo_agents.config import TruncationConfig
    from nemo_oo_agents.runtime.actor import ActorRuntime
    from nemo_oo_agents.runtime.context_manager import ContextManager
    from nemo_oo_agents.runtime.event_manager import EventManager

    class _StandaloneAgent:
        """Minimal agent stub: no framework blocks, fresh state per call."""

        event_query = None
        _execution_config = None

        def __init__(self, llm: Any) -> None:
            self._llm = llm
            self._truncation = TruncationConfig()
            self.render_config = RenderConfig()
            self.event_manager = EventManager()
            self.context_manager = ContextManager()
            self.runtime = ActorRuntime(self)

    cls = type("_StandaloneAgent", (_StandaloneAgent,), {})
    # Setting __module__ makes inspect.getmodule(cls) return the function's module,
    # so execute_code's filter_module_globals exposes the caller's types.
    cls.__module__ = module_name
    _agent_cls_cache[module_name] = cls
    return cls


def _make_adapter(func: Callable[..., Any], strategy: Any = None) -> Callable[..., Any]:
    """Create a method-like adapter (with *self*) from a standalone function.

    ActorRuntime._execute_with_generation expects a method whose first parameter
    is ``self`` (it skips it when building CurrentCall).  This adapter:

    - prepends ``self`` to the signature so the runtime parses args correctly
    - has an ``...`` body so ``has_ellipsis_body`` returns True
    - shares the original function's ``__globals__`` so ``get_type_hints`` can
      resolve forward references (PEP 563 / ``from __future__ import annotations``)
    - carries all ``_plan_*`` / ``_strategy_*`` metadata the runtime reads
    """
    orig_sig = inspect.signature(func)
    new_params = [
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        *orig_sig.parameters.values(),
    ]
    new_sig = orig_sig.replace(parameters=new_params)

    # Template coroutine with an ellipsis body — has_ellipsis_body detects this.
    # Reuse func's own __globals__ so get_type_hints resolves annotations in
    # the same scope the function was defined in. Works for module-level
    # functions and for REPL-defined functions (where func.__module__ is None).
    async def _tmpl(self: Any, *args: Any, **kwargs: Any) -> Any: ...

    adapted = types.FunctionType(_tmpl.__code__, func.__globals__, func.__name__)

    adapted.__doc__ = func.__doc__
    adapted.__name__ = func.__name__
    adapted.__qualname__ = func.__qualname__
    adapted.__annotations__ = dict(func.__annotations__)
    adapted.__signature__ = new_sig  # type: ignore[attr-defined]
    adapted.__module__ = func.__module__

    # Metadata that _execute_with_generation reads off the method object
    adapted._plan_llm = getattr(func, "_plan_llm", None)  # type: ignore[attr-defined]
    adapted._plan_strategy = strategy or getattr(func, "_plan_strategy", None)  # type: ignore[attr-defined]
    adapted._strategy_context = getattr(func, "_strategy_context", None)  # type: ignore[attr-defined]
    adapted._strategy_events = getattr(func, "_strategy_events", None)  # type: ignore[attr-defined]
    adapted._needs_generation = True  # type: ignore[attr-defined]
    adapted._agent_decorator = "auto"  # type: ignore[attr-defined]

    return adapted


def create_standalone_wrapper(
    func: Callable[..., Any],
    strategy: Any,
    llm: Any,
) -> Callable[..., Any]:
    """Wrap a standalone generation function so it can be called directly.

    Each invocation creates a fresh agent stub (no shared state, history resets).
    LLM resolution order: ``@strategy(llm=…)`` → parent-agent cascade → RuntimeError.

    Args:
        func: Original async function with an ellipsis body.
        strategy: GenerationStrategy instance resolved from the decorator.
        llm: Optional explicit LLM from ``@strategy(..., llm=...)``.

    Returns:
        Async callable with the same signature as *func*.
    """

    # Build the adapter once — func and strategy are decoration-time constants.
    _adapted = _make_adapter(func, strategy=strategy)

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        resolved_llm = llm
        if resolved_llm is None:
            # Cascade: inherit LLM from a calling agent if we're inside one
            from nemo_oo_agents.runtime.context_vars import _parent_agent_var

            parent = _parent_agent_var.get()
            if parent is not None:
                resolved_llm = getattr(parent, "_llm", None)
        if resolved_llm is None:
            raise RuntimeError(
                f"No LLM client for standalone function '{func.__name__}'. "
                f"Pass llm=<client> to @strategy(..., llm=<client>)."
            )

        # Fresh agent per call — no shared state, history resets automatically
        agent_cls = _get_agent_cls(func.__module__)
        agent = agent_cls(llm=resolved_llm)

        return await agent.runtime._execute_with_generation(_adapted, args, kwargs, func.__name__)

    wrapper._standalone = True  # type: ignore[attr-defined]
    wrapper._needs_generation = True  # type: ignore[attr-defined]
    wrapper._plan_strategy = strategy  # type: ignore[attr-defined]
    wrapper._plan_llm = llm  # type: ignore[attr-defined]
    return wrapper
