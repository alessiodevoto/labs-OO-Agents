# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Terminal Bench 2 agent for nemo-oo-agents-benchmarks.

Ported and adapted from:
  agent006/experiments/evaluation-ablations/agents/terminal_bench_agent.py
  (git show 7c42c254:experiments/evaluation-ablations/agents/terminal_bench_agent.py)

Terminal Bench 2 is a harder, higher-quality version of Terminal Bench 1.
Same concept — real-world CLI tasks evaluated inside Docker/Apptainer
containers — but with more tasks, higher difficulty, and stricter grading.
Official Harbor dataset: terminal-bench@2.0
  github.com/laude-institute/terminal-bench-2.git

Architecture:
- Single CodeAct agent with dynamic terminal-tool context injection
- More iterations (150 vs 100) to handle harder multi-step tasks
- Explicit verification step before declaring completion
- Structured hints about difficulty and verification requirements
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from context_blocks import DynamicContext
from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.agentdoc import spec
from nemo_oo_agents.config import CodeActConfig
from unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)


class TerminalBench2Agent(
    Agent,
    llm=FakeLLMClient(),
    context={"terminal_tools": DynamicContext(expr="doc(self.terminal)")},
):
    """Terminal Bench 2 agent — harder real-world CLI tasks in Docker containers.

    Terminal Bench 2 features more tasks and higher difficulty than Terminal
    Bench 1.  Tasks span system administration, security, data science, coding,
    networking, databases, and more.  Each task is graded by an automated
    pytest test suite inside the container.

    The agent uses a dynamic context block (``terminal_tools``) so the LLM
    always sees the current API of ``self.terminal`` (injected by the runner).
    """

    terminal: Any  # TerminalBenchTools injected at runtime by the Harbor runner

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        spec(self, "context", hidden=False)

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point for the Harbor evaluation framework.

        Extracts task fields, populates context blocks, and calls
        ``solve_task``.  Returns a dict with ``response``, ``success``, and
        optionally ``error``.
        """
        self.instructions: str = task_input.get("system_prompt", "")
        self.context["instructions"] = self.instructions

        self.initial_observation: str = task_input.get("initial_observation", "")
        self.context["initial_observation"] = self.initial_observation

        self.problem_statement: str = task_input.get("user_message", "")
        self.context["problem_statement"] = self.problem_statement

        try:
            result = await self.solve_task(self.problem_statement)
            return {
                "response": str(result) if result is not None else "",
                "success": result is not None,
                "result": result,
            }
        except Exception as e:
            logger.error("TerminalBench2Agent failed: %s", e)
            return {"response": "", "success": False, "error": str(e)}

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=150, max_retries=5)))
    async def _solve_task(self, description: str) -> Any:
        """Complete a Terminal Bench 2 task inside the Docker container.

        You are an expert system administrator and software engineer working
        inside a Linux Docker container.  Your working directory is /app.

        ## Difficulty Notice
        Terminal Bench 2 tasks are harder than Terminal Bench 1.  Expect
        multi-step workflows, obscure configuration requirements, and strict
        automated grading.  Do NOT assume the obvious solution works — verify
        every step.

        ## Task
        {description}

        ## Instructions
        - Use ``await self.terminal.execute("command")`` to run shell commands.
        - Always check command output before proceeding to the next step.
        - You have root access; install packages with ``apt-get install -y``.
        - Read task instructions carefully — grading is strict and automated.
        - Common tools available: bash, git, python3, curl, wget, make, etc.

        ## Mandatory Verification
        Before calling ``return_result()``:
        1. Re-read the original task requirements.
        2. Run a final check to confirm the expected artefacts are in place.
        3. If the task includes a test script (e.g. ``run-tests.sh`` or
           ``pytest`` files), run it and confirm it passes.
        4. Only declare success when you have evidence — do NOT assume.

        ## Common Patterns
        ```python
        # Run a shell command and inspect output
        out = await self.terminal.execute("ls -la /app")
        print(out)

        # Install a missing dependency
        await self.terminal.execute("apt-get install -y -q some-package")

        # Run a test to verify work
        out = await self.terminal.execute("bash run-tests.sh")
        print(out)

        # Run pytest directly
        out = await self.terminal.execute("pytest -v")
        print(out)
        ```

        Use ``doc(self)`` to see all available tools and methods.
        Only call ``return_result()`` when the task is fully complete and
        verified.
        """
        ...

    async def solve_task(self, description: str) -> Any:
        """Wrapper around ``_solve_task`` with top-level exception handling."""
        try:
            return await self._solve_task(description)
        except Exception as e:
            logger.error("Error solving Terminal Bench 2 task: %s", e)
            return None
