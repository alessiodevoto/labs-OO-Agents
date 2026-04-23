# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
ReAct baseline agent for nemo-oo-agents-benchmarks.

Ported from agent006/.worktrees/memory-ci/experiments/evaluation-ablations/
agents/baseline_react.py — standard Thought/Action/Observation loop agent
used as the comparison baseline in the nemo-oo-agents tech report (Table 1).

Architecture:
- Classic ReAct loop: Thought → Action → Observation, repeated
- Calls Harbor environment tools via method dispatch (not execute_python)
- One action per turn; LLM must emit Final Answer to exit
- Works with any Harbor benchmark — tools are injected by the runner

Key differences from the baseline (CodeAct) agent:
- No execute_python() sandbox — each tool call is a discrete method invocation
- Explicit Thought/Action/Action Input/Observation turn structure
- Stop sequences prevent Claude from generating XML-style tool calls
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import textwrap
import types
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

from nemo_oo_agents import Agent
from unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

_MAX_OBSERVATION_CHARS = 10_000
_STOP_SEQUENCES = [
    "Observation:",
    "<function_calls>",
    "<reify",
    "<Thought>",
    "<Action>",
    "<Action Input>",
    "<Final Answer>",
    "<Observation>",
    "<observation>",
]


def _truncate(text: str, max_chars: int = _MAX_OBSERVATION_CHARS) -> str:
    """Trim *text* to *max_chars* and append a truncation notice when clipped."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... truncated, {len(text) - max_chars} more chars ...]"


class _ToolRegistry:
    """Dispatches tool calls from the ReAct loop to Harbor environment methods."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}
        self._descriptions: list[dict] = []

    def register_instance(self, instance: Any, prefix: str = "") -> None:
        """Register all public async/sync methods of *instance* as tools."""
        for name in dir(instance):
            if name.startswith("_"):
                continue
            method = getattr(instance, name)
            if not callable(method):
                continue
            tool_name = f"{prefix}{name}" if prefix else name
            doc = (method.__doc__ or f"Call {name}").strip().split("\n")[0]
            params = _extract_params(method)
            self._tools[tool_name] = method
            self._descriptions.append({"name": tool_name, "description": doc, "parameters": params})

    def tool_list(self) -> str:
        """Human-readable tool list for the system prompt."""
        lines = ["Available tools:"]
        for t in self._descriptions:
            parts = []
            for k, v in t["parameters"].items():
                parts.append(f"{k}: {v.get('type', 'any')}")
            sig = ", ".join(parts)
            lines.append(f"  - {t['name']}({sig}): {t['description']}")
        return "\n".join(lines)

    async def call(self, name: str, **kwargs: Any) -> str:
        """Invoke a registered tool and return its output as a string."""
        if name not in self._tools:
            available = list(self._tools)
            return f"Error: unknown tool '{name}'. Available: {available}"
        try:
            # Coerce kwargs to declared Python types
            tool_def = next((t for t in self._descriptions if t["name"] == name), None)
            if tool_def:
                kwargs = _coerce_kwargs(kwargs, tool_def["parameters"])
            fn = self._tools[name]
            result = fn(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            return _truncate(str(result))
        except Exception as exc:
            return f"Error calling {name}: {exc}"


def _extract_params(method: Any) -> dict[str, dict]:
    """Build a simple JSON-schema-ish param dict from type annotations."""
    annotations = getattr(method, "__annotations__", {})
    params: dict[str, dict] = {}
    for param_name, param_type in annotations.items():
        if param_name == "return":
            continue
        # Unwrap Optional[T] / Union[T, None] → use the non-None inner type
        origin = get_origin(param_type)
        if origin is Union or isinstance(param_type, types.UnionType):
            inner = [a for a in get_args(param_type) if a is not type(None)]
            param_type = inner[0] if inner else param_type
            origin = get_origin(param_type)
        if origin is list:
            params[param_name] = {"type": "array", "python_type": "list"}
        elif origin is dict:
            params[param_name] = {"type": "object", "python_type": "dict"}
        else:
            type_str = getattr(param_type, "__name__", str(param_type))
            if type_str == "int":
                params[param_name] = {"type": "number", "python_type": "int"}
            elif type_str == "float":
                params[param_name] = {"type": "number", "python_type": "float"}
            elif type_str == "bool":
                params[param_name] = {"type": "boolean", "python_type": "bool"}
            else:
                params[param_name] = {"type": "string", "python_type": "str"}
    return params


def _coerce_kwargs(kwargs: dict, param_defs: dict) -> dict:
    """Cast string values in *kwargs* to the Python types declared in *param_defs*."""
    result = dict(kwargs)
    for k, v in result.items():
        pdef = param_defs.get(k, {})
        python_type = pdef.get("python_type", "str")
        if isinstance(v, str):
            try:
                if python_type == "int":
                    result[k] = int(v)
                elif python_type == "float":
                    result[k] = float(v)
                elif python_type == "bool":
                    result[k] = v.lower() in ("true", "1", "yes")
                elif python_type == "list" and v.startswith("["):
                    result[k] = json.loads(v)
                elif python_type == "dict" and v.startswith("{"):
                    result[k] = json.loads(v)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
    return result


def _parse_action(
    text: str,
) -> tuple[str | None, dict | None, str | None, str | None]:
    """Extract (action_name, action_input, final_answer, parse_error) from LLM text."""
    # Final answer
    match = re.search(r"Final Answer:\s*(.+)", text, re.DOTALL)
    if match:
        return None, None, match.group(1).strip(), None

    # Action name
    action_match = re.search(r"Action:\s*([\w.]+)", text)
    if not action_match:
        return None, None, None, None
    action_name = action_match.group(1)

    # Action input (JSON)
    input_match = re.search(r"Action Input:\s*", text)
    if input_match:
        try:
            action_input, _ = json.JSONDecoder().raw_decode(text, input_match.end())
        except json.JSONDecodeError as exc:
            raw = text[input_match.end() : input_match.end() + 300]
            return (
                action_name,
                None,
                None,
                (
                    f"JSON parse error: {exc}\nYour Action Input: {raw}\n"
                    "Fix: ensure Action Input is a valid JSON object on one line."
                ),
            )
    else:
        action_input = {}

    return action_name, action_input, None, None


_SYSTEM_PROMPT_TEMPLATE = """\
You are a ReAct (Reasoning + Acting) agent that solves tasks step by step.

{tool_list}

To use a tool, always respond with exactly this format:
Thought: <your reasoning>
Action: <tool_name>
Action Input: {{"param1": "value1"}}

After receiving an Observation, continue the loop.
When the task is complete, respond with:
Thought: I now have the answer.
Final Answer: <your final answer>

Rules:
- One Action per turn.
- Do NOT use XML tags or native function-call format.
- Always start with "Thought:".
"""


class ReActBaselineAgent(Agent, llm=FakeLLMClient()):
    """General-purpose ReAct baseline agent.

    Implements the classic Thought/Action/Observation loop using discrete
    Harbor environment tool calls (no Python sandbox).  Intended for
    cross-benchmark comparison with the CodeAct-based ``baseline`` agent.

    Tools are injected by the Harbor runner via ``task_input["environment_tools"]``.
    """

    def __init__(
        self,
        llm: UnifiedLLM | None = None,
        max_iterations: int = 100,
        **kwargs: Any,
    ) -> None:
        super().__init__(llm=llm, **kwargs)
        self.max_iterations = max_iterations

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner."""
        # Build tool registry from environment tools
        registry = _ToolRegistry()
        for tool_name in task_input.get("environment_tools", []):
            tool = getattr(self, tool_name, None)
            if tool is not None:
                prefix = f"{tool_name}_"
                registry.register_instance(tool, prefix=prefix)

        # Build prompt parts
        system_content = _SYSTEM_PROMPT_TEMPLATE.format(tool_list=registry.tool_list())

        if system_prompt := task_input.get("system_prompt"):
            system_content += f"\n=== Domain Context ===\n{system_prompt}"

        if initial_obs := task_input.get("initial_observation"):
            system_content += f"\n\n=== Current State ===\n{initial_obs}"

        description = (
            task_input.get("user_message")
            or task_input.get("user_prompt")
            or task_input.get("description", "")
        )
        response_format = task_input.get("response_format", "")
        if response_format:
            description += f"\n\nExpected output format: {response_format}"

        messages: list[dict] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": description or "Please solve this task."},
        ]

        try:
            result = await self._react_loop(messages, registry, response_format)
            return result
        except Exception as exc:
            logger.exception("ReActBaselineAgent._run_evaluation failed")
            return {"response": "", "success": False, "error": str(exc)}

    async def _react_loop(
        self,
        messages: list[dict],
        registry: _ToolRegistry,
        response_format: str,
    ) -> dict:
        """Run the Thought/Action/Observation loop until Final Answer or max_iterations."""
        for iteration in range(1, self.max_iterations + 1):
            try:
                llm_response = await self._llm.acall(
                    messages=messages,
                    stop=_STOP_SEQUENCES,
                )
                assistant_text = llm_response.message or ""
            except Exception as exc:
                return {"response": "", "success": False, "error": f"LLM error: {exc}"}

            messages.append({"role": "assistant", "content": assistant_text})

            # Reject multiple actions per turn
            if len(re.findall(r"\bAction:", assistant_text)) > 1:
                obs = "Observation: ERROR: Multiple actions detected. Submit exactly one Action per turn."
                messages.append({"role": "user", "content": obs})
                continue

            action_name, action_input, final_answer, parse_error = _parse_action(assistant_text)

            if final_answer is not None:
                result_str = final_answer
                if response_format == "code":
                    result_str = textwrap.dedent(result_str)
                return {
                    "response": result_str,
                    "success": True,
                    "iterations": iteration,
                }

            if parse_error:
                messages.append({"role": "user", "content": f"Observation: {parse_error}"})
            elif action_name is not None:
                observation = await registry.call(action_name, **(action_input or {}))
                messages.append({"role": "user", "content": f"Observation: {observation}"})
            else:
                # No action, no final answer — nudge the model
                messages.append(
                    {
                        "role": "user",
                        "content": "Please continue. Use an Action or provide a Final Answer.",
                    }
                )

        return {
            "response": "",
            "success": False,
            "error": f"Max iterations ({self.max_iterations}) reached without Final Answer.",
        }
