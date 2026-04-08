# SPDX-License-Identifier: Apache-2.0
"""NAT tool bridge: turn NAT Functions into native Python async methods.

Each NAT Function becomes a simple async callable that the LLM can use
directly:  `result = await self.current_datetime()`

The generated wrappers are fully introspectable by agentdoc -- they look
identical to hand-written async methods with proper signatures, type
annotations, and docstrings.

The core generation functions use lazy imports so they can be tested
without NAT installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nat.builder.builder import Builder

logger = logging.getLogger(__name__)

# Pydantic sentinel for missing defaults
try:
    from pydantic_core import PydanticUndefined
except ImportError:
    PydanticUndefined = ...  # fallback sentinel

# Parameter names that NAT tools use as dummies (no semantic meaning)
_DUMMY_PARAM_NAMES = frozenset({"unused", "dummy", "_unused", "_dummy"})


def _type_to_str(annotation: Any) -> str:
    """Convert a type annotation to its string representation."""
    if annotation is None:
        return "Any"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    # Handle generic types like list[str], Optional[int], etc.
    return str(annotation).replace("typing.", "")


def create_tool_method(tool_name: str, nat_fn: Any) -> tuple[str, Any]:
    """Turn a NAT Function into a native async Python method.

    The generated method:
    - Has the tool_name as its function name (so `self.current_datetime()` works)
    - Filters out dummy parameters (e.g., NAT's `unused: str`)
    - Has a proper typed signature from the input schema
    - Delegates to `self._nat_fns[tool_name].ainvoke()`

    Args:
        tool_name: The NAT function name (becomes the Python method name)
        nat_fn: The NAT Function to wrap

    Returns:
        Tuple of (tool_name, async method function)
    """
    description = nat_fn.description or f"Call the {tool_name} tool"

    # Extract real parameters from the Function's Pydantic input schema,
    # filtering out dummy/unused params that some NAT tools require.
    schema = nat_fn.input_schema
    fields = schema.model_fields  # dict[str, FieldInfo]

    params = []
    param_names = []

    for fname, finfo in fields.items():
        if fname.lower() in _DUMMY_PARAM_NAMES:
            continue
        type_name = _type_to_str(finfo.annotation)
        has_default = finfo.default is not None and finfo.default is not PydanticUndefined
        if has_default:
            params.append(f"{fname}: {type_name} = {repr(finfo.default)}")
        else:
            params.append(f"{fname}: {type_name}")
        param_names.append(fname)

    param_str = ", ".join(params)
    kwargs_build = ", ".join(f'"{n}": {n}' for n in param_names)

    # Escape description for use in triple-quoted string
    safe_desc = description.replace('"""', r"\"\"\"").replace("\\", "\\\\")

    # Generate the async method via exec() -- same technique as dataclasses/attrs.
    # The method reads the NAT Function from self._nat_fns[tool_name].
    safe_name = tool_name  # already snake_case from NAT
    if param_names:
        func_code = f'''
async def {safe_name}(self, {param_str}) -> str:
    """{safe_desc}"""
    return str(await self._nat_fns["{safe_name}"].ainvoke({{{kwargs_build}}}))
'''
    else:
        func_code = f'''
async def {safe_name}(self) -> str:
    """{safe_desc}"""
    return str(await self._nat_fns["{safe_name}"].ainvoke(""))
'''

    namespace: dict[str, Any] = {}
    exec(func_code, namespace)  # noqa: S102
    method = namespace[safe_name]

    return safe_name, method


async def inject_nat_tools(
    agent: Any,
    tool_names: list[str],
    builder: Builder,
) -> None:
    """Inject NAT tools onto an NeMo OO Agents agent as native async methods.

    For each tool name, resolves the NAT Function from the builder,
    generates a native async method, and sets it directly on the agent's
    class.  The LLM sees them via doc(self) and calls them like:

        result = await self.current_datetime()
        result = await self.search(query="hello")

    Args:
        agent: The NeMo OO Agents agent instance
        tool_names: List of NAT tool names to inject
        builder: The NAT builder for resolving tools
    """
    agent_cls = type(agent)

    # Ensure the class has a _nat_fns dict to hold the backing Functions
    if not hasattr(agent_cls, "_nat_fns"):
        agent_cls._nat_fns = {}

    for tool_name in tool_names:
        try:
            # Get the raw NAT Function from the builder
            nat_fn = await builder.get_function(tool_name)

            # Generate a native async method
            method_name, method = create_tool_method(tool_name, nat_fn)

            # Store the NAT Function so the method can call it
            agent_cls._nat_fns[method_name] = nat_fn

            # Set the method on the CLASS so doc(self) / agentdoc sees it
            setattr(agent_cls, method_name, method)

            logger.info(
                "Injected NAT tool '%s' as async method onto %s",
                tool_name,
                agent_cls.__name__,
            )
        except Exception as e:
            logger.warning(
                "Could not inject NAT tool '%s': %s",
                tool_name,
                e,
            )
