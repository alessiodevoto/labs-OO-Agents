# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generation strategies for nemo_oo_agents.

One class = one strategy. Each strategy owns its configuration.
"""

from contextvars import ContextVar

from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.strategies.base import GenerationStrategy, RuntimeServices
from nemo_oo_agents.strategies.codeact import CodeActStrategy
from nemo_oo_agents.strategies.codeact_lite import CodeActLiteStrategy
from nemo_oo_agents.strategies.composite import CompositeStrategy
from nemo_oo_agents.strategies.current_call import CurrentCall
from nemo_oo_agents.strategies.predict import PredictStrategy
from nemo_oo_agents.strategies.prefill import InspectInputsPrefill, Prefill
from nemo_oo_agents.strategies.pure_python import PurePythonStrategy
from nemo_oo_agents.strategies.reflexion import ReflexionStrategy
from nemo_oo_agents.strategies.template import TemplateStrategy

# =============================================================================
# Default Strategy Override
# =============================================================================
# Context variable for overriding the default strategy globally.
# When None (default), get_default_strategy() returns PurePythonStrategy().
# Use set_default_strategy() to override for all agents in the current context.

_default_strategy_var: ContextVar[GenerationStrategy | None] = ContextVar(
    "default_strategy", default=None
)


def get_default_strategy() -> GenerationStrategy:
    """Get the default strategy for agents without an explicit strategy.

    Returns the strategy set via set_default_strategy(), or creates a fresh
    CodeActStrategy() instance if not set.

    Returns:
        GenerationStrategy instance to use as default

    Example:
        # In actor.py / decorators.py:
        strategy = call_strategy or decorator_strategy or get_default_strategy()
    """
    strategy = _default_strategy_var.get()
    if strategy is None:
        return CodeActStrategy(config=CodeActConfig())
    return strategy


def set_default_strategy(strategy: GenerationStrategy | None) -> None:
    """Set the default strategy for all agents in the current async context.

    This allows overriding the default strategy (PurePythonStrategy) without
    modifying agent classes. Useful for:
    - Evaluation pipelines that want to test different strategies
    - Testing with a specific strategy across all agents
    - Temporarily switching strategies for a block of code

    Args:
        strategy: GenerationStrategy instance to use as default, or None to
                  reset to PurePythonStrategy (the library default)

    Example:
        from nemo_oo_agents import set_default_strategy, CodeActStrategy
        from nemo_oo_agents.config import CodeActConfig

        # Override default for all agents
        set_default_strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))

        # Run evaluation - all agents use CodeActStrategy
        results = await evaluator.run()

        # Reset to library default
        set_default_strategy(None)
    """
    _default_strategy_var.set(strategy)


__all__ = [
    "GenerationStrategy",
    "RuntimeServices",
    "CurrentCall",
    "CompositeStrategy",
    "TemplateStrategy",
    "PurePythonStrategy",
    "CodeActStrategy",
    "CodeActLiteStrategy",
    "ReflexionStrategy",
    "PredictStrategy",
    # Prefill plugins
    "Prefill",
    "InspectInputsPrefill",
    # Default strategy functions
    "get_default_strategy",
    "set_default_strategy",
]
