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

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.context_blocks import DynamicContext
from nemo_oo_agents.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a software engineer interacting continuously with a computer by \
submitting commands. You will be helping implement necessary changes to \
meet requirements in the problem description. Your task is specifically to \
make changes to non-test files in the current directory in order to fix the \
issue described in the problem statement in a way that is general and \
consistent with the codebase.

The repository is checked out at /testbed. A conda environment named \
"testbed" is pre-activated with all dependencies installed. The current \
working directory is /testbed.\
"""

# Ported from agent006/.worktrees/memory-ci/evaluation/adapters/swebench.py
# _get_environment_instructions() — this is what produced 74.20% ReAct baseline.
_ENVIRONMENT_INSTRUCTIONS = """\
## Environment

You are in a Docker container with the repository already cloned and checked \
out at /testbed. Use the available tools to explore the codebase, understand \
the bug, and make targeted fixes.

## Recommended Workflow

1. **Analyze** the codebase by finding and reading relevant files
2. **Reproduce** the issue with a test script to understand the bug
3. **Identify** the root cause by examining the implementation
4. **Implement** the fix in the source code
5. **Verify** your fix works by running your reproduction script again
6. **Test** thoroughly — run the relevant test suite to ensure no regressions
7. **Submit** your patch when complete and verified

## Boundaries

- MODIFY: Regular source code files in /testbed
- DO NOT MODIFY: Tests, configuration files (pyproject.toml, setup.cfg, etc.)

## Think About Test Coverage

Before submitting, ask yourself:
- Does my fix address the root cause, not just the symptom?
- Are there edge cases I should handle? (None values, empty strings, type mismatches, etc.)
- Consider the issue title and description — think critically, do not follow suggestions blindly.

## Final Evaluation

Your final changes will be automatically evaluated. The evaluation system will:
1. Capture the final state of your working directory
2. Generate a unified diff patch of all changes
3. Run the test suite to verify your fix

You can check your progress at any point by running:
```bash
git diff HEAD
```

Make sure all changes are saved to files before finishing. The evaluation \
tests whatever modifications exist in /testbed at the end of your session.
"""


_ISSUE_DESCRIPTION_HEADER = "## Issue Description"


def _format_problem_statement(raw: str) -> str:
    """Wrap a raw Harbor problem statement with context headers."""
    return f"{_ISSUE_DESCRIPTION_HEADER}\n\n{raw.strip()}\n\n{_ENVIRONMENT_INSTRUCTIONS}"


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
            # Harbor sends the raw instruction.md content as user_message;
            # inject environment context so the agent knows where it is and
            # how to proceed (this is what produced 74.20% with the 006 ReAct
            # baseline — see gl-64).
            self.instructions = _SYSTEM_PROMPT
            self.problem_statement = _format_problem_statement(task_input["user_message"])
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
