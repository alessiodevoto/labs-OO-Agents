# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Generic benchmark agent for code and system tasks.

A single, non-specialized CodeAct agent that works on any task requiring shell
access inside a container. Not tuned for any particular benchmark — the same
agent handles SWE-bench, Terminal-Bench, or any Harbor-compatible task.

Core contract:
- ``self.shell`` for persistent shell access (run/read/replace/write_file)
- ``self.verify(cmd)`` to gate the return: agent must run a real test/check
  and see it pass before ``return_result`` is honored
- ``self.todo`` for optional structured progress tracking
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

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

    ## Verification Gate (ENFORCED)

    You MUST call ``await self.verify("<test/check command>")`` before finishing.
    The gate blocks ``return_result`` until your most recent ``verify()`` passes.
    A hand-rolled script that just prints "PASSED" does NOT count — run a real
    test or acceptance check whose exit code reflects reality.

    ## Tools

    ```python
    r = await self.shell.run("command")                # persistent shell
    r = await self.shell.read("file.py", lines=(1,50)) # view → Match
    await self.shell.replace(r, new_code)              # edit at Match
    await self.shell.write_file("file.py", content)    # create/overwrite
    await self.verify("pytest tests/ -x")              # gate-satisfying check
    ```

    ## Workflow

    1. **Understand** — explore the codebase/environment, reproduce the issue
    2. **Implement** — make the fix or complete the task
    3. **Verify** — run a real test via ``self.verify()`` and see it pass
    4. **Return** — ``return_result("done")`` only after verify passes

    Use ``self.todo`` to track progress on multi-step tasks.
    Mark todos done as you complete them.
    """

    _MAX_VERIFY_BOUNCES = 3

    terminal: Any = None  # Injected by some runners; None otherwise

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        cwd = next((d for d in ("/testbed", "/app") if os.path.isdir(d)), os.getcwd())
        self.shell = _make_shell(cwd)
        self.todo = TodoManager()
        self.problem_statement = ""
        self._last_verify: dict[str, Any] | None = None
        self._verify_bounces = 0
        self._pending_nudge: str | None = None
        self.context_manager.set_static("self.shell", doc(type(self.shell)))

    @hidden
    def _parse_test_outcome(self, result: Any) -> dict[str, Any]:
        """Best-effort pass/fail parse of a test/check run's output.

        Recognizes pytest, Django's runtests, unittest, and generic rc-based outcomes.
        Errs toward ``passed=False`` when ambiguous.
        """
        text = getattr(result, "text", None) or getattr(result, "stdout", None) or str(result)
        low = text.lower()
        n_failed = n_passed = 0
        m = re.search(r"(\d+) failed", low)
        if m:
            n_failed = int(m.group(1))
        m = re.search(r"(\d+) passed", low)
        if m:
            n_passed = int(m.group(1))
        failed_markers = (
            "failed (",
            "errors=",
            "failures=",
            "traceback",
            "no such file",
            "command not found",
            "collected 0 items",
            " fail",
            "assertionerror",
        )
        rc = getattr(result, "returncode", None)
        looks_failed = (
            n_failed > 0 or (rc not in (0, None)) or any(mk in low for mk in failed_markers)
        )
        looks_passed = (n_passed > 0 and n_failed == 0) or low.rstrip().endswith("ok") or rc == 0
        passed = bool(looks_passed and not looks_failed)
        return {
            "passed": passed,
            "n_passed": n_passed,
            "n_failed": n_failed,
            "returncode": rc,
            "preview": text[-500:],
        }

    async def verify(self, check_cmd: str) -> str:
        """Run a test or acceptance check and record the outcome for the return gate.

        THIS is the only way to satisfy the gate that guards ``return_result``.
        Run the real test command through here — the gate opens only when the
        most recent ``verify()`` shows a pass.

        Args:
            check_cmd: Shell command that runs the test/check (should exit non-zero
                on failure).

        Returns:
            A short status line (passed / failed + counts).
        """
        if self.terminal is not None:
            result = await self.terminal.execute(check_cmd)
        else:
            result = await self.shell.run(check_cmd, timeout=600)
        outcome = self._parse_test_outcome(result)
        outcome["cmd"] = check_cmd
        self._last_verify = outcome
        verdict = "PASSED" if outcome["passed"] else "FAILED"
        msg = (
            f"verify [{verdict}]  cmd={check_cmd!r}  "
            f"passed={outcome['n_passed']} failed={outcome['n_failed']} rc={outcome['returncode']}"
        )
        print(msg)
        print(result)
        return msg

    def _verification_gate(self) -> str | None:
        """Return a nudge if the agent must NOT stop yet, else None."""
        if self._last_verify is None:
            return (
                "STOP REJECTED: you never ran a verification check this session. "
                "Run ``await self.verify('<test command>')`` with a real test or "
                "acceptance check and see it pass before finishing."
            )
        if not self._last_verify["passed"]:
            lv = self._last_verify
            return (
                f"STOP REJECTED: your last ``verify()`` FAILED "
                f"(passed={lv['n_passed']} failed={lv['n_failed']}, cmd={lv['cmd']!r}). "
                "Fix the implementation until that check passes."
            )
        return None

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner."""
        self._last_verify = None
        self._verify_bounces = 0
        self._pending_nudge = None

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
            result = await self.solve_task(self.problem_statement)
            result_str = str(result) if result is not None else ""
            return {"response": result_str, "success": True, "result": result}
        except Exception as e:
            logger.error("BenchAgent failed: %s", e)
            return {"response": "", "success": False, "error": str(e)}

    async def solve_task(self, description: str) -> str:
        """Run the task behind the verification gate.

        Bounces premature returns up to ``_MAX_VERIFY_BOUNCES`` times, then
        lets the honest unverified result stand.
        """
        result = None
        desc = description
        for attempt in range(self._MAX_VERIFY_BOUNCES + 1):
            try:
                result = await self._solve_task(desc)
            except Exception as e:
                logger.error("Error solving task: %s", e)
                result = None

            nudge = self._verification_gate()
            if nudge is None:
                return str(result) if result is not None else "done"

            if attempt >= self._MAX_VERIFY_BOUNCES:
                logger.warning("Verification gate exhausted bounces; returning unverified.")
                return str(result) if result is not None else "done"
            self._verify_bounces += 1
            logger.warning(
                "Verification gate bounced return (attempt %d/%d): %s",
                self._verify_bounces,
                self._MAX_VERIFY_BOUNCES,
                nudge.split(":", 1)[0],
            )
            self._pending_nudge = nudge
            desc = (
                f"{description}\n\n"
                f"=== RETURN BLOCKED BY VERIFICATION GATE ===\n{nudge}\n"
                "You CANNOT finish until your most recent ``self.verify(<cmd>)`` "
                "shows a passing check. Resolve the blocker above first."
            )

        return str(result) if result is not None else "done"

    @strategy(
        CodeActStrategy(
            config=CodeActConfig(
                max_iterations=300, max_retries=10, text_only_stop_behavior="synthetic_reasoning"
            )
        )
    )
    async def _solve_task(self, description: str) -> str:
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
        - Read task instructions carefully — grading is strict and automated.

        ## Mandatory Verification (ENFORCED)

        Before finishing, you MUST run a real test or acceptance check via:

            await self.verify("<test command>")

        The check must genuinely exercise the work and exit non-zero on failure
        (pytest / make / a build check / a validation script). A script that
        merely prints "PASS" without exercising real outputs does NOT count.

        If you ``return_result(...)`` without a passing ``self.verify()`` this
        session, the gate REJECTS the return and bounces you back.

        ## Workflow

        1. Explore and understand the task/codebase
        2. Implement the solution
        3. Verify via ``self.verify(...)``
        4. Only then ``return_result("done")``

        Use ``doc(self)`` to see all available tools and methods.
        """
        ...
