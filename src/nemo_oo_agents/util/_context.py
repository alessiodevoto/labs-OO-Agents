"""Internal context management for utility modules."""

from contextvars import ContextVar
from typing import Any

# ContextVar to store current agent during execution
_current_agent_var: ContextVar[Any] = ContextVar("current_agent", default=None)

# ContextVar to store current runtime during execution (for child agent inheritance)
_current_runtime_var: ContextVar[Any] = ContextVar("current_runtime", default=None)


def _set_current_agent(agent: Any) -> None:
    """Set the current agent (called during code execution)."""
    _current_agent_var.set(agent)


def _current_agent() -> Any:
    """
    Get the current agent instance.

    This is used by utility modules (message, context, logger, task) to access
    the agent without requiring it to be passed as an argument.

    Returns:
        Current agent instance

    Raises:
        RuntimeError: If called outside of agent execution context
    """
    agent = _current_agent_var.get()
    if agent is None:
        raise RuntimeError(
            "No agent in context. Utility modules can only be used from generated code."
        )
    return agent
