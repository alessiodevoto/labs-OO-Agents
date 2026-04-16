# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
SWEBench basic agent for nemo-oo-agents-benchmarks.

Single-generation-method agent that uses CodeAct to solve SWEBench tasks
with shell tools available via ``self.swebench``.

Adapted from ``harbor-agent006/agents/swebench_basic.py`` (originally by Gaia).
"""

from __future__ import annotations

import logging
import textwrap
from typing import TYPE_CHECKING, Any

from context_blocks import DynamicContext
from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.config import CodeActConfig
from unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a software engineer working inside a pre-configured repository "
    "container.  Your task is to make changes to non-test files in order to "
    "fix the issue described in the problem statement in a way that is general "
    "and consistent with the codebase.\n\n"
    "The repository is checked out at /testbed.  A conda environment named "
    "'testbed' is pre-activated with all dependencies installed.  Use the "
    "available shell and file tools to navigate, understand, and fix the code."
)


class SWEBenchBasicAgent(
    Agent,
    llm=FakeLLMClient(),
    context={"swebench_tools": DynamicContext(expr="doc(self.swebench)")},
):
    """SWEBench basic agent: single solve loop with shell tool access."""

    swebench: Any  # SWEBenchLocalTools injected at runtime by the runner

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner.

        Accepts the unified runner interface ``{"user_message": instruction}``
        as well as the legacy field-by-field format.
        """
        if "user_message" in task_input:
            # Unified interface from the benchmark-agnostic runner.
            self.instructions = _SYSTEM_PROMPT
            self.problem_statement = task_input["user_message"]
            self.response_format = "diff"
            self.initial_observation = ""
        else:
            self.instructions = task_input.get("system_prompt", "")
            self.initial_observation = task_input.get("initial_observation", "")
            self.problem_statement = task_input.get("problem_statement", "")
            self.response_format = task_input.get("response_format", "")

        self.context["instructions"] = self.instructions
        self.context["initial_observation"] = self.initial_observation
        self.context["problem_statement"] = self.problem_statement
        self.context["response_format"] = self.response_format

        try:
            result = await self.solve_task(self.problem_statement, self.response_format)
            result_str = str(result) if result is not None else ""
            if self.response_format == "code" and result_str:
                result_str = textwrap.dedent(result_str)
            return {"response": result_str, "success": True, "result": result}
        except Exception as e:
            return {"response": "", "success": False, "error": str(e)}

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=250, max_retries=10)))
    async def _solve_task(self, description: str, response_format: str = "") -> Any:
        """Solve the task.

        Instructions:
        - For environment tasks: use ``await self.swebench.<method>(...)`` directly
          in ``execute_python()`` blocks; those run inside the container.
          Direct commands (no ``await``) run on the host.
        - Use the REPL to iterate and validate your solution before returning.
        - ``return`` / ``return_result`` ends execution and returns the result.
          Only use it when you have the final answer.
        - Check ``doc(self)`` for available tools and methods.
        - Pin useful state:
          ``self.context.set_dynamic("key", "python_expression")``
          Example::

            directory_structure = await self.swebench.execute(
                "ls -la | grep -v '^\\\\.' | grep -v '__pycache__'"
            )
            self.context.set_dynamic("directory_structure", "directory_structure")

        - Variables persist across REPL turns.
        - Output is auto-truncated; use slices or grep to inspect long output.
        """
        ...

    async def solve_task(self, description: str, response_format: str = "") -> Any:
        """Public wrapper: calls :meth:`_solve_task`, falls back to ``git diff HEAD``."""
        try:
            return await self._solve_task(description, response_format)
        except Exception as e:
            logger.error("Error solving task: %s", e)
        return await self.swebench.execute("git diff HEAD")
