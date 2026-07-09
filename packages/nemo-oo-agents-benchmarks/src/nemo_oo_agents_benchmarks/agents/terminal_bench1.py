# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Terminal Bench 1 agent — ported from agent006.

Ported from:
  agent006/experiments/evaluation-ablations/agents/terminal_bench_agent.py
  (class TerminalBenchAgent, commit 7c42c254)

Terminal Bench 1 benchmark: real-world command-line tasks executed in Docker
sandboxes.  Categories include system administration, security, data science,
and file operations.  Tasks provide a natural-language instruction; the agent
must solve it by executing shell commands.  Tasks are scored by automated
pytest verifiers.

Key design choices:
- ``self.terminal`` is injected by the Harbor runner at task setup time
- DynamicContext exposes terminal tool docs so the LLM always sees the live API
- max_iterations=100 gives ample budget for multi-step shell tasks
- The system prompt emphasises incremental verification before returning
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nooa import Agent, CodeActStrategy, strategy
from nooa.config import CodeActConfig
from nooa.context_blocks import DynamicContext
from nooa.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nooa.unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert system administrator and shell scripter working inside "
    "a Linux Docker container.  Your working directory is /app.\n\n"
    "You have root access and can install packages with apt-get.  Common tools "
    "available: bash, git, python3, curl, wget, apt-get, find, grep, awk, sed, "
    "tar, zip, openssl, and more.\n\n"
    "Work incrementally: run a command, inspect its output, then decide the "
    "next step.  Verify your work before calling return_result()."
)


class TerminalBench1Agent(
    Agent,
    llm=FakeLLMClient(),
    context={"terminal_tools": DynamicContext(expr="doc(self.terminal)")},
):
    """Terminal Bench 1 agent: single solve loop with Docker shell tool access.

    Executes real-world command-line tasks in a sandboxed Docker container.
    The ``self.terminal`` tool is injected by the Harbor runner at task setup.
    """

    terminal: Any  # TerminalBenchTools injected at runtime by the runner

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner.

        Accepts the unified runner interface ``{"user_message": instruction}``
        as well as the field-by-field format used in the original Terminal Bench
        adapter (system_prompt / initial_observation / user_message).
        """
        if "user_message" in task_input:
            self.instructions = task_input.get("system_prompt", _SYSTEM_PROMPT)
            self.problem_statement = task_input["user_message"]
            self.initial_observation = task_input.get("initial_observation", "")
        else:
            self.instructions = task_input.get("system_prompt", _SYSTEM_PROMPT)
            self.initial_observation = task_input.get("initial_observation", "")
            self.problem_statement = (
                task_input.get("user_message")
                or task_input.get("user_prompt")
                or task_input.get("description", "")
            )

        self.context["instructions"] = self.instructions
        self.context["initial_observation"] = self.initial_observation
        self.context["problem_statement"] = self.problem_statement

        try:
            result = await self.solve_task(self.problem_statement)
            return {
                "response": str(result) if result is not None else "",
                "success": result is not None,
                "result": result,
            }
        except Exception as e:
            logger.error("Error in TerminalBench1Agent._run_evaluation: %s", e)
            return {"response": "", "success": False, "error": str(e)}

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100, max_retries=5)))
    async def _solve_task(self, description: str) -> Any:
        """Complete the terminal task.

        ## Task
        {description}

        ## Instructions
        - Use ``await self.terminal.execute("command")`` to run shell commands.
          Commands run **inside the Docker container**, not on the host.
        - Inspect command output before proceeding to the next step.
        - You have root access; install packages with ``apt-get install -y``.
        - Complete the task incrementally, verifying each step.
        - Use ``doc(self)`` to see all available tools and methods.
        - Only call ``return_result()`` when the task is fully complete and
          verified.

        ## Examples
        ```python
        # Explore the environment
        output = await self.terminal.execute("ls -la /app")
        print(output)

        # Install a missing package
        output = await self.terminal.execute("apt-get install -y -q jq")
        print(output)

        # Run a script and check its output
        output = await self.terminal.execute("python3 /app/script.py")
        print(output)

        # Verify file was created
        output = await self.terminal.execute("ls -lh /app/result.txt")
        print(output)
        ```
        """
        ...

    async def solve_task(self, description: str) -> Any:
        """Public wrapper: calls :meth:`_solve_task` and logs any exception."""
        try:
            return await self._solve_task(description)
        except Exception as e:
            logger.error("Error solving Terminal Bench 1 task: %s", e)
            return None
