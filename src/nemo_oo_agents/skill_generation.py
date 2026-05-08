# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Skill generation method wiring — route @strategy methods on Skills through the agent runtime.

When a Skill has methods decorated with ``@strategy`` that have ellipsis bodies,
those methods are generation methods. When the skill is attached to an agent,
these methods are wrapped so that calling ``agent.my_skill.do_it(...)`` routes
through the parent agent's runtime — same LLM, event history, context, etc.

The skill method is invoked as if it were defined on the agent itself.
Both CodeActStrategy and PredictStrategy work.

Implementation:
- ``is_generation_method(obj)`` — detect @strategy-decorated ellipsis methods
- ``_make_skill_adapter(func, strategy)`` — create a method-like adapter (self=agent)
- ``_wrap_skill_generation_method(skill, func, strategy)`` — create the callable wrapper
- ``bind_generation_methods(skill, agent)`` — scan a skill and wrap all generation methods
"""

import inspect
import types
from collections.abc import Callable
from functools import wraps
from typing import Any


def is_generation_method(obj: Any) -> bool:
    """Return True if *obj* is a @strategy-decorated async method needing generation.

    Checks for the ``_needs_generation`` flag set by the @strategy decorator or
    the metaclass, OR detects async functions with ``_strategy_override`` and
    ellipsis bodies.
    """
    if not inspect.iscoroutinefunction(obj):
        return False
    # Already wrapped by @strategy decorator
    if getattr(obj, "_needs_generation", False):
        return True
    # Has strategy override and an ellipsis body (e.g. defined on a plain class)
    if hasattr(obj, "_strategy_override"):
        from nemo_oo_agents.ellipsis_detection import has_ellipsis_body

        return has_ellipsis_body(obj)
    return False


def _make_skill_adapter(func: Callable, strategy: Any = None) -> Callable:
    """Create a method-like adapter (with agent *self*) from a skill generation method.

    Similar to standalone.py's _make_adapter but for skill methods.
    The adapter has:
    - ``self`` as first parameter (will be the agent)
    - The original method's parameters (minus the skill's ``self``)
    - The original docstring, annotations, return type
    - All ``_plan_*`` / ``_strategy_*`` metadata the runtime reads
    """
    # Get the original function (unwrap if it was wrapped by @strategy)
    original = getattr(func, "__wrapped__", func)
    if hasattr(func, "_original"):
        original = func._original

    orig_sig = inspect.signature(original)
    # Skip the first 'self' parameter (which was the skill's self)
    params = list(orig_sig.parameters.values())
    if params and params[0].name == "self":
        params = params[1:]

    # Build new signature: agent self + original params (minus skill self)
    new_params = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)] + params
    new_sig = orig_sig.replace(parameters=new_params)

    # Template coroutine with an ellipsis body — has_ellipsis_body detects this.
    async def _tmpl(self: Any, *args: Any, **kwargs: Any) -> Any: ...

    # Use func's module globals so get_type_hints resolves annotations correctly
    import sys as _sys

    func_module = getattr(original, "__module__", None)
    func_globals = (
        vars(_sys.modules[func_module]) if func_module and func_module in _sys.modules else {}
    )

    adapted = types.FunctionType(_tmpl.__code__, func_globals, original.__name__)

    adapted.__doc__ = original.__doc__
    adapted.__name__ = original.__name__
    adapted.__qualname__ = original.__qualname__
    adapted.__annotations__ = dict(getattr(original, "__annotations__", {}))
    adapted.__signature__ = new_sig  # type: ignore[attr-defined]
    adapted.__module__ = func_module  # type: ignore[attr-defined]

    # Metadata that _execute_with_generation reads off the method object
    adapted._plan_llm = getattr(func, "_plan_llm", None)  # type: ignore[attr-defined]
    adapted._plan_strategy = (
        strategy
        or getattr(func, "_plan_strategy", None)
        or getattr(func, "_strategy_override", None)
    )  # type: ignore[attr-defined]
    adapted._strategy_context = getattr(func, "_strategy_context", None)  # type: ignore[attr-defined]
    adapted._strategy_events = getattr(func, "_strategy_events", None)  # type: ignore[attr-defined]
    adapted._strategy_truncation = getattr(func, "_strategy_truncation", None)  # type: ignore[attr-defined]
    adapted._needs_generation = True  # type: ignore[attr-defined]
    adapted._agent_decorator = "auto"  # type: ignore[attr-defined]

    return adapted


def _wrap_skill_generation_method(
    skill: Any,
    method_name: str,
    func: Callable,
    strategy: Any = None,
) -> Callable:
    """Create a wrapper that routes a skill generation method through the parent agent's runtime.

    The returned callable has the same signature as the original method (minus self),
    and when called, delegates to ``agent.runtime._execute_with_generation()``.

    Args:
        skill: The Skill instance
        method_name: Name of the method
        func: The original generation method (bound or unbound)
        strategy: Strategy instance (if known)

    Returns:
        Async callable wrapping the generation method
    """
    # Build the adapter once
    adapted = _make_skill_adapter(func, strategy=strategy)

    # Determine the original function for wraps
    original = getattr(func, "__wrapped__", func)

    @wraps(original)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        agent = getattr(skill, "_agent", None)
        if agent is None:
            raise RuntimeError(
                f"Skill generation method '{method_name}' cannot execute: "
                f"skill is not attached to an agent. "
                f"Call skill.attach(agent) or install the skill on an agent first."
            )

        # Use the agent's runtime to execute the generation
        return await agent.runtime._execute_with_generation(adapted, args, kwargs, method_name)

    wrapper._needs_generation = True  # type: ignore[attr-defined]
    wrapper._plan_strategy = adapted._plan_strategy  # type: ignore[attr-defined]
    wrapper._plan_llm = adapted._plan_llm  # type: ignore[attr-defined]
    wrapper._skill_generation = True  # type: ignore[attr-defined]

    return wrapper


def bind_generation_methods(skill: Any, agent: Any) -> list[str]:
    """Scan a skill for generation methods and bind them to the parent agent's runtime.

    Sets ``skill._agent = agent`` and replaces each generation method on the skill
    instance with a wrapper that routes through the agent's runtime.

    Args:
        skill: A Skill instance being attached to an agent
        agent: The parent Agent instance

    Returns:
        List of method names that were bound as generation methods
    """
    skill._agent = agent
    bound_methods: list[str] = []

    # Scan the skill's class for generation methods
    for name in dir(type(skill)):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(type(skill), name, None)
        except Exception:
            continue
        if attr is None:
            continue

        # Check if it's a generation method
        if not is_generation_method(attr):
            continue

        # Get strategy
        strat = getattr(attr, "_plan_strategy", None) or getattr(attr, "_strategy_override", None)

        # Create the wrapper and bind it to the skill instance
        wrapper = _wrap_skill_generation_method(skill, name, attr, strategy=strat)
        setattr(skill, name, wrapper)
        bound_methods.append(name)

    return bound_methods
