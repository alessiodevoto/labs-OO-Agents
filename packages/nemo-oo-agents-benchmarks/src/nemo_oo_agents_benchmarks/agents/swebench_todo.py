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
import os
import textwrap
from typing import TYPE_CHECKING, Any

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.agentdoc import doc, hidden
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.context_blocks import DynamicContext
from nemo_oo_agents.tools.shell_tools import ShellTools
from nemo_oo_agents.tools.shell_tools_legacy import ShellToolsLegacy
from nemo_oo_agents.tools.todo import TodoManager
from nemo_oo_agents.unifiedllm import FakeLLMClient

_CONDA_ACTIVATE = "export PATH=/opt/harbor/cpython312/bin:$PATH; source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed 2>/dev/null || true"


@hidden
def _make_shell(cwd: str = "/testbed"):
    """Construct the shell. ``SHELL_VARIANT=legacy`` selects the old bash-based
    ``ShellToolsLegacy``; anything else (default) uses the canonical
    ``ShellTools`` (formerly the "v5" bake-off winner, now the only modern shell).
    """
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


if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a software engineer working inside a pre-configured \
container. Your task is to make changes to fix the issue described in the \
problem statement in a way that is general and consistent with the codebase.

The working directory is pre-set to the project root. A conda environment \
may be pre-activated with dependencies installed.\
"""

_ENVIRONMENT_INSTRUCTIONS = """## Environment

You are in a Docker container with the repository already cloned and checked \
out at /testbed. You have a persistent shell session — cd, environment \
variables, and working directory all survive across calls.

⚠️ **Project imports will NOT work in your code cells.** Your Python sandbox \
does not have the repository installed. To run project code or tests, always \
use `await self.shell.run("python -", stdin=script)` or `await self.shell.run("pytest ...")`.

## Boundaries

- MODIFY: Regular source code files in /testbed
- DO NOT MODIFY: Tests, configuration files (pyproject.toml, setup.cfg, etc.)

## Final Evaluation

Your final changes are evaluated **in place**: the harness runs the
hidden test suite directly against /testbed at the end of your session.
Your file edits ARE the submission — there is no patch to produce and no
staging step. Just make sure every change is written to disk before you
finish.

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
        "todo_status": DynamicContext(expr="self.todo.status()"),
        "task": DynamicContext(expr="self.problem_statement"),
    },
):
    """SWE-bench agent with todo-driven structured workflow.

    Follow the pre-filled todos in order. Mark each done before starting the next.
    DO NOT jump straight to editing files — understand the problem first.

    ## Workflow — Think Before You Code

    DO NOT jump straight to editing files. Follow the pre-filled todo phases:

    ### Phase 1: Understand (spend at least 5-10 turns here)

    1a. **Explore**: List the tree and find source/test files (see the
        `self.shell` block for your shell's exact API). Identify where
        source and tests live.

    1b. **Reproduce**: Run the specific failing test(s) to see the actual error.
        Use `await self.shell.run("pytest tests/test_foo.py -x -v")`.

    1c. **Read test code**: Read the failing test file. Understand WHAT
        behaviour the test expects.

    1d. **Trace root cause**: Search the source for the buggy symbols to find
        the buggy code.
        Read the source. Don't just look at the error location — trace backwards
        to find WHERE the bug originates.

    1e. **Summarise**: Write a clear root-cause description in a todo comment.
        What's broken, where, and what the fix should be.

    ### Phase 2: Implement

    Fix ALL affected files. Don't just fix one file. Check imports, exports,
    related methods. Use your shell's targeted-edit method (see the `self.shell` block) for changes.
    Validate after EACH edit — run the failing test immediately.

    ### Phase 3: Verify (DO NOT SKIP)

    3a. **Run failing tests**: Confirm they pass.
    3b. **Run related tests**: Check for regressions.
    3c. **Review diff**: `await self.shell.run("git diff HEAD")` — no debug prints,
        no spurious whitespace changes, no unnecessary modifications.

    ## Tool examples

    ```python
    # Shell operations (persistent session — cd/env survive). Your shell's
    # EXACT method surface is in the `self.shell` block (doc(type(self.shell)));
    # use only what it lists. The core four:
    r = await self.shell.run("pytest tests/test_foo.py -x")        # any shell command
    r = await self.shell.read("src/module.py", lines=(10, 60))     # numbered window -> editable anchor
    await self.shell.replace(r, new_code)                          # edit the read() region (no copy-paste of old text)
    await self.shell.write_file("src/new.py", content)             # create/overwrite (no quoting)
    # Search-then-edit: run() a plain grep, then edit the hits directly via .matches —
    # no re-grep, no string-guessing (which is the usual cause of "old text not found").
    r = await self.shell.run("grep -rn 'def foo' src/")            # plain search -> r.matches
    await self.shell.replace(r.matches[0], new_code)               # edit the first hit by anchor
    ```

    ```python
    # Todo tracking — mark phases done as you go
    print(self.todo.status())
    self.todo.done("abc123")
    self.todo.add("sub-task")
    self.todo.comment("abc123", "found the bug in line 42")
    ```

    ## Rules

    - Mark each todo done as you complete it (BEFORE starting the next)
    - Only `return_result("done")` when tests pass and the worktree is clean
    - If your first approach fails after 3 attempts, try a different angle
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
        self.shell = _make_shell("/testbed")
        self.todo = TodoManager()
        self.problem_statement = ""
        self.context_manager.set_static("self.shell", doc(type(self.shell)))

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

        # Determine working directory. Explicit working_dir is strict — a missing
        # dir is a misconfig, not an intent to create one, and running the whole
        # task from the wrong cwd silently corrupts results (false zeros).
        cwd = task_input.get("working_dir")
        if cwd:
            if not os.path.isdir(cwd):
                raise ValueError(f"working_dir does not exist: {cwd!r}")
        else:
            # Auto-detect the container layout (SWE-bench: /testbed, TB: /app),
            # falling back to the current directory for non-container runs.
            cwd = next((d for d in ("/testbed", "/app") if os.path.isdir(d)), os.getcwd())

        # Reset stateful tools so each evaluation starts clean.
        self.shell = _make_shell(cwd)
        self.context_manager.set_static("self.shell", doc(type(self.shell)))
        self.todo.clear()

        # Activate the testbed conda env if available (no-op on non-conda containers).
        await self.shell.run(_CONDA_ACTIVATE)

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

    @strategy(
        CodeActStrategy(
            config=CodeActConfig(
                max_iterations=300, max_retries=10, text_only_stop_behavior="synthetic_reasoning"
            )
        )
    )
    async def _solve_task(self, description: str, response_format: str = "") -> str:
        """Solve the task using the structured todo-driven workflow.

        Follow the phases in self.todo. Mark each done as you complete it.
        Only return when tests pass and the worktree is clean.

        The harness grades the working tree in place — your file edits in
        /testbed ARE the submission. There is no patch to return and no
        staging step. When the fix is verified, just
        ``return_result("done")`` (any short status string). Do NOT paste a
        diff, do NOT call ``git add``, do NOT construct a result object.
        """
        # Start with Phase 1a: explore the repo structure
        r = await self.shell.run("ls -la")
        print("=== Repo Structure ===")
        print(r)
        print("\n=== Todo Status ===")
        print(self.todo.status())
        ...

    async def solve_task(self, description: str, response_format: str = "") -> str:
        """Public wrapper: run the workflow, then confirm the worktree carries edits.

        The harbor verifier grades the working tree in place (it runs the
        task's own test script against /testbed) — it never reads a returned
        patch. So there is nothing to extract or submit: the agent's job is
        simply to leave correct edits in the tree. We just sanity-check that
        edits exist and return a short status; an empty diff is logged as a
        warning (the run will score 0, but that's a real signal, not a
        submission-protocol bug).
        """
        try:
            result = await self._solve_task(description, response_format)
        except Exception as e:
            logger.error("Error solving task: %s", e)
            result = None

        if response_format != "diff":
            return str(result) if result is not None else ""

        r = await self.shell.run("git diff HEAD")
        if not r.text.strip():
            logger.warning("git diff HEAD is empty — agent left no edits in the worktree")
        return str(result) if result is not None else "done"
