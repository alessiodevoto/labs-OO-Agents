"""Generation strategies for agent006.

One class = one strategy. Each strategy owns its configuration.
"""

from contextvars import ContextVar

from agent006.config import CodeActConfig
from agent006.strategies.base import GenerationStrategy, RuntimeServices
from agent006.strategies.codeact import CodeActStrategy
from agent006.strategies.codeact_lite import CodeActLiteStrategy
from agent006.strategies.composite import CompositeStrategy
from agent006.strategies.current_call import CurrentCall
from agent006.strategies.predict import PredictStrategy
from agent006.strategies.prefill import InspectInputsPrefill, Prefill
from agent006.strategies.pure_python import PurePythonStrategy
from agent006.strategies.reflexion import ReflexionStrategy
from agent006.strategies.template import TemplateStrategy

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
        from agent006 import set_default_strategy, CodeActStrategy
        from agent006.config import CodeActConfig

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
