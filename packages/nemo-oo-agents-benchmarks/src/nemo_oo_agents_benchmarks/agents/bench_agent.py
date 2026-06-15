# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Generic benchmark agent for code and system tasks.

A single, non-specialized CodeAct agent that works on any task requiring shell
access inside a container. Not tuned for any particular benchmark -- the same
agent handles SWE-bench, Terminal-Bench, or any Harbor-compatible task.

Core contract:
- ``self.shell`` for persistent shell access (run/read/replace/write_file)
- ``self.todo`` for optional structured progress tracking
- Structured return: the agent must declare solution_description, evidence,
  and command_to_verify when finishing -- forcing reflection before return.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.agentdoc import doc, hidden
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.context_blocks import DynamicContext
from nemo_oo_agents.tools.shell_tools import ShellTools
from nemo_oo_agents.tools.shell_tools_legacy import ShellToolsLegacy
from nemo_oo_agents.tools.todo import TodoManager
from nemo_oo_agents.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

_CONDA_ACTIVATE = (
    "export PATH=/opt/harbor/cpython312/bin:$PATH; "
    "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed 2>/dev/null || true"
)


class TaskResult(BaseModel):
    """Structured result the agent must return when finishing a task."""

    solution_description: str = Field(
        description="What you did and why it solves the problem. Describe root cause and fix."
    )
    evidence: str = Field(
        description=(
            "Concrete evidence that the task is done: what tests passed, "
            "what output was produced, what behavior changed. Not a guess -- "
            "cite the actual shell output you observed."
        )
    )
    command_to_verify: str = Field(
        description="A shell command a verifier can run to confirm correctness (exit 0 on success)."
    )


@hidden
def _make_shell(cwd: str = "/") -> ShellTools | ShellToolsLegacy:
    """Construct the shell variant based on SHELL_VARIANT env."""
    raw = os.environ.get("SHELL_VARIANT", "").strip().lower()
    variant = "legacy" if raw == "legacy" else "default"
    if raw and raw not in ("legacy", "default", "1", "5"):
        logger.warning("Unknown SHELL_VARIANT=%r; using the default shell", raw)
    try:
        from nemo_oo_agents.runtime.harness_metrics import get_harness_metrics

        get_harness_metrics().set_shell_variant(variant)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to tag shell_variant in harness metrics", exc_info=True)
    shell = ShellToolsLegacy(cwd=cwd) if variant == "legacy" else ShellTools(cwd=cwd)
    shell._init_command = _CONDA_ACTIVATE
    return shell


class BenchAgent(
    Agent,
    llm=FakeLLMClient(),
    context={
        "todo_status": DynamicContext(expr="self.todo.status()"),
        "task": DynamicContext(expr="self.problem_statement"),
    },
):
    """Generic agent for code and system tasks in containers.

    ## Tools

    ```python
    r = await self.shell.run("command")                # persistent shell
    r = await self.shell.read("file.py", lines=(1,50)) # view -> Match
    await self.shell.replace(r, new_code)              # edit at Match
    await self.shell.write_file("file.py", content)    # create/overwrite
    ```

    ## Workflow

    1. **Understand** -- explore the codebase/environment, reproduce the issue
    2. **Implement** -- make the fix or complete the task
    3. **Verify** -- run the relevant tests and confirm they pass
    4. **Return** -- ``return_result(TaskResult(...))`` with evidence

    ## Return format

    When you are done, you MUST return a ``TaskResult``:

    ```python
    return_result(TaskResult(
        solution_description="Root cause: missing URL-encoding in auth.py. Fixed with quote_plus().",
        evidence="pytest tests/test_login.py passed (3 passed in 0.4s)",
        command_to_verify="pytest tests/test_login.py -x",
    ))
    ```

    Use ``self.todo`` to track progress on multi-step tasks.
    Mark todos done as you complete them.
    """

    terminal: Any = None  # Injected by some runners; None otherwise

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        cwd = next((d for d in ("/testbed", "/app") if os.path.isdir(d)), os.getcwd())
        self.shell = _make_shell(cwd)
        self.todo = TodoManager()
        self.problem_statement = ""
        self.context_manager.set_static("self.shell", doc(type(self.shell)))

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner."""
        # Read task fields generically (Harbor adapters vary in field names).
        self.problem_statement = (
            task_input.get("user_message")
            or task_input.get("problem_statement")
            or task_input.get("task_description")
            or ""
        )
        instructions = task_input.get("system_prompt") or task_input.get("instructions") or ""
        initial_obs = task_input.get("initial_observation") or ""

        if instructions:
            self.context["instructions"] = instructions
        if initial_obs:
            self.context["initial_observation"] = initial_obs

        # Reset shell to the task working dir.
        cwd = task_input.get("working_dir")
        if cwd:
            if not os.path.isdir(cwd):
                raise ValueError(f"working_dir does not exist: {cwd!r}")
        else:
            cwd = next((d for d in ("/testbed", "/app") if os.path.isdir(d)), os.getcwd())
        self.shell = _make_shell(cwd)
        self.context_manager.set_static("self.shell", doc(type(self.shell)))
        self.todo.clear()
        await self.shell.run(_CONDA_ACTIVATE)

        try:
            result = await self._solve_task(self.problem_statement)
            if isinstance(result, TaskResult):
                return {
                    "response": result.command_to_verify,
                    "success": bool(result.solution_description),
                    "result": result.model_dump(),
                }
            # Fallback for non-structured returns
            result_str = str(result) if result is not None else ""
            return {"response": result_str, "success": True, "result": result}
        except Exception as e:
            logger.error("BenchAgent failed: %s", e)
            return {"response": "", "success": False, "error": str(e)}

    @strategy(
        CodeActStrategy(
            config=CodeActConfig(
                max_iterations=300, max_retries=10, text_only_stop_behavior="synthetic_reasoning"
            )
        )
    )
    async def _solve_task(self, description: str) -> TaskResult:
        """Solve the task.

        You are an expert software engineer and system administrator working
        inside a Linux container. Solve the task described below.

        ## Task
        {description}

        ## Instructions
        - Use ``await self.shell.run("command")`` to run shell commands.
        - Use ``await self.shell.read("path")`` to view files.
        - Use ``await self.shell.replace(...)`` to edit files.
        - Use ``await self.shell.write_file(path, content)`` to create files.
        - Use ``self.todo`` to track progress on multi-step work.
        - You have root access; install packages as needed.
        - Read task instructions carefully -- grading is strict and automated.

        ## Verification & Return

        Before finishing, run the relevant tests to confirm your work is correct.
        Then return a structured result explaining WHY you believe the task is done:

        ```python
        return_result(TaskResult(
            solution_description="The login handler didn't escape special chars in emails. Fixed by adding quote_plus() in auth.py:42.",
            evidence="pytest tests/test_login.py -x passed: 5 passed in 1.2s",
            command_to_verify="pytest tests/test_login.py -x",
        ))
        ```

        ## Workflow

        1. Explore and understand the task/codebase
        2. Implement the solution
        3. Run tests to verify
        4. Return ``TaskResult(...)`` with concrete evidence

        Use ``doc(self)`` to see all available tools and methods.
        """
        ...
