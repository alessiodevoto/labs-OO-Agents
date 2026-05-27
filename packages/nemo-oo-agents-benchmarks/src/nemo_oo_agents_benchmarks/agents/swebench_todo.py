# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
SWE-bench Verified agent with structured todo-driven workflow.

Single-agent approach using ShellTools and TodoManager for structured
progress tracking. Runs inside the Harbor container with the repository
at /testbed.

Key differences from swebench_basic:
- Pre-filled todo phases guide the agent through a proven workflow
- ShellTools provides persistent shell state, file ops, ripgrep, and fuzzy edit
- TodoManager enforces discipline: explore → reproduce → trace → fix → verify
"""

from __future__ import annotations

import logging
import textwrap
from typing import TYPE_CHECKING, Any

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.context_blocks import DynamicContext
from nemo_oo_agents.tools.shell_tools import ShellTools
from nemo_oo_agents.tools.todo import TodoManager
from nemo_oo_agents.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a software engineer working inside a pre-configured repository \
container. Your task is to make changes to non-test files in order to fix \
the issue described in the problem statement in a way that is general and \
consistent with the codebase.

The repository is checked out at /testbed. A conda environment named \
"testbed" is pre-activated with all dependencies installed. The current \
working directory is /testbed.\
"""

_ENVIRONMENT_INSTRUCTIONS = """## Environment

You are in a Docker container with the repository already cloned and checked \
out at /testbed. You have a persistent shell session — cd, environment \
variables, and working directory all survive across calls.

## Boundaries

- MODIFY: Regular source code files in /testbed
- DO NOT MODIFY: Tests, configuration files (pyproject.toml, setup.cfg, etc.)

## Final Evaluation

Your final changes will be automatically evaluated. The evaluation system will:
1. Capture the final state of your working directory
2. Generate a unified diff patch of all changes
3. Run the test suite to verify your fix

Make sure all changes are saved to files before finishing. The evaluation \
tests whatever modifications exist in /testbed at the end of your session.\
"""

_ISSUE_DESCRIPTION_HEADER = "## Issue Description"


def _format_problem_statement(raw: str) -> str:
    """Wrap a raw Harbor problem statement with context headers."""
    return f"{_ISSUE_DESCRIPTION_HEADER}\n\n{raw.strip()}\n\n{_ENVIRONMENT_INSTRUCTIONS}"


class SWEBenchTodoAgent(
    Agent,
    llm=FakeLLMClient(),
    context={
        "shell_tools": DynamicContext(expr="doc(type(self.shell))"),
        "todo_status": DynamicContext(expr="self.todo.status()"),
        "task": DynamicContext(expr="self.problem_statement"),
    },
):
    """SWE-bench agent with todo-driven structured workflow.

    Uses ShellTools for persistent shell + file operations and TodoManager
    for progress tracking through a proven explore → reproduce → trace →
    fix → verify workflow.

    ## Workflow — Think Before You Code

    DO NOT jump straight to editing files. Follow the pre-filled todo phases:

    ### Phase 1: Understand (spend at least 5-10 turns here)

    1a. **Explore**: Use `self.shell.ls(".", depth=3)` and
        `self.shell.run("find . -name '*.py' -not -path './.git/*' | head -50")`
        to understand project structure. Identify where source and tests live.

    1b. **Reproduce**: Run the specific failing test(s) to see the actual error.
        Use `await self.shell.run("pytest tests/test_foo.py -x -v")`.

    1c. **Read test code**: Use `await self.shell.view("tests/test_foo.py")` on
        the failing test. Understand WHAT behaviour the test expects.

    1d. **Trace root cause**: Use `await self.shell.grep("def function_name", "src/")`
        and `await self.shell.grep("ClassName", ".")` to find the buggy code.
        Read the source. Don't just look at the error location — trace backwards
        to find WHERE the bug originates.

    1e. **Summarise**: Write a clear root-cause description in a todo comment.
        What's broken, where, and what the fix should be.

    ### Phase 2: Implement

    Fix ALL affected files. Don't just fix one file. Check imports, exports,
    related methods. Use `await self.shell.edit(path, old, new)` for targeted changes.
    Validate after EACH edit — run the failing test immediately.

    ### Phase 3: Verify (DO NOT SKIP)

    3a. **Run failing tests**: Confirm they pass.
    3b. **Run related tests**: Check for regressions.
    3c. **Review diff**: `await self.shell.run("git diff HEAD")` — no debug prints,
        no spurious whitespace changes, no unnecessary modifications.

    ## Tool examples

    ```python
    # Shell operations (persistent session — cd/env survive)
    r = await self.shell.run("pytest tests/test_foo.py -x")
    r = await self.shell.view("src/module.py", offset=10, limit=50)
    r = await self.shell.edit("src/module.py", old_code, new_code)
    r = await self.shell.write("src/new.py", content)
    r = await self.shell.grep("pattern", "src/")
    r = await self.shell.find("*.py", "src/")
    r = await self.shell.ls("src/", depth=2)
    ```

    ```python
    # Todo tracking — mark phases done as you go
    print(self.todo.status())
    self.todo.done("abc123")
    self.todo.add("sub-task")
    self.todo.comment("abc123", "found the bug in line 42")
    ```

    ## Rules

    - Mark each todo phase done as you complete it (BEFORE starting the next)
    - Only `return` / `return_result()` when tests pass and diff is clean
    - If your first approach fails, try a different angle — don't give up
    - Pin useful state: `self.context.set_dynamic("key", "expression")`
    - Variables persist across REPL turns
    - Output is auto-truncated; use slices or grep to inspect long output

    ## Common mistakes to avoid

    - Editing a file without reading it first
    - Fixing the symptom instead of the root cause
    - Forgetting to update `__init__.py` exports when adding new functions/classes
    - Returning before running tests to verify
    - Giving up too early — if your first approach fails, try a different angle
    """

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        self.shell = ShellTools(cwd="/testbed")
        self.todo = TodoManager()
        self.problem_statement = ""

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner.

        Accepts the unified runner interface ``{"user_message": instruction}``
        as well as the legacy field-by-field format.
        """
        if "user_message" in task_input:
            self.instructions = _SYSTEM_PROMPT
            self.problem_statement = _format_problem_statement(task_input["user_message"])
            self.response_format = "diff"
            self.initial_observation = ""
        else:
            self.instructions = task_input.get("system_prompt", "")
            self.initial_observation = task_input.get("initial_observation", "")
            self.problem_statement = task_input.get("problem_statement", "")
            self.response_format = task_input.get("response_format", "")

        # Reset stateful tools so each evaluation starts clean.
        self.shell = ShellTools(cwd="/testbed")
        self.todo.clear()

        # Set context blocks unconditionally (clear stale values from prior runs).
        self.context["initial_observation"] = self.initial_observation or None
        self.context["response_format"] = self.response_format or None

        # Pre-fill todos for the standard SWE-bench workflow
        t1 = self.todo.add("Phase 1a: Explore — repo structure, key files, test layout")
        t2 = self.todo.add("Phase 1b: Reproduce — run failing tests, read the error", deps=[t1.id])
        t3 = self.todo.add(
            "Phase 1c: Read test code — understand what the tests expect", deps=[t2.id]
        )
        t4 = self.todo.add("Phase 1d: Trace root cause — find the bug in source code", deps=[t3.id])
        t5 = self.todo.add(
            "Phase 1e: Summarise — rewrite the issue as a root-cause description",
            deps=[t4.id],
        )
        t6 = self.todo.add("Phase 2: Implement — fix all affected files", deps=[t5.id])
        t7 = self.todo.add("Phase 3a: Verify — run failing tests, confirm they pass", deps=[t6.id])
        t8 = self.todo.add("Phase 3b: Regressions — run related tests", deps=[t7.id])
        self.todo.add(
            "Phase 3c: Review diff — git diff, no debug prints or spurious changes",
            deps=[t8.id],
        )

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
        """Solve the task using the structured todo-driven workflow.

        Follow the phases in self.todo. Mark each done as you complete it.
        Only return when tests pass and diff is clean.
        """
        # Start with Phase 1a: explore the repo structure
        r = await self.shell.ls(".", depth=3)
        print("=== Repo Structure ===")
        print(r.text)
        print("\n=== Todo Status ===")
        print(self.todo.status())
        ...

    async def solve_task(self, description: str, response_format: str = "") -> Any:
        """Public wrapper: calls _solve_task, falls back to git diff HEAD."""
        try:
            result = await self._solve_task(description, response_format)
            # If response_format is 'diff' but agent returned prose, use git diff
            result_str = str(result) if result is not None else ""
            if response_format == "diff" and result_str and "diff --git" not in result_str:
                logger.warning("Agent returned prose instead of diff, falling back to git diff")
                r = await self.shell.run("git diff HEAD")
                if r.text.strip():
                    return r.text
            return result
        except Exception as e:
            logger.error("Error solving task: %s", e)
        r = await self.shell.run("git diff HEAD")
        return r.text
