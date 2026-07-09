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
import re
from typing import TYPE_CHECKING, Any

from nemo_oo_agents import Agent, CodeActStrategy, strategy
from nemo_oo_agents.agentdoc import spec
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.context_blocks import DynamicContext
from nemo_oo_agents.unifiedllm import FakeLLMClient

if TYPE_CHECKING:
    from nemo_oo_agents.unifiedllm import UnifiedLLM

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

    # Max times solve_task() will bounce a premature return before letting the
    # (honest) unverified result stand.
    _MAX_VERIFY_BOUNCES = 3

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        spec(self, "context", hidden=False)
        # Verification-gate state. ``_last_verify`` records the most recent
        # acceptance check the agent ran via ``self.verify()`` this session;
        # the gate opens only when it shows a pass. Unlike SWE-bench there is no
        # git worktree to diff and no pre-existing failing test — TB tasks are
        # graded by held-out tests uploaded only at verifier time. So the gate
        # enforces "you ran a real acceptance check and it passed", where the
        # check is something the agent can run NOW (build succeeds, a self-test
        # passes, required artifacts exist and conform).
        self._last_verify: dict[str, Any] | None = None
        self._verify_bounces = 0

    @staticmethod
    def _parse_check_outcome(output: Any) -> dict[str, Any]:
        """Best-effort pass/fail parse of an acceptance-check run.

        Recognizes pytest, generic "PASS/FAIL" markers, and non-zero exit. Errs
        toward ``passed=False`` when ambiguous — the gate opens only on a clearly
        successful check, never on a maybe.
        """
        text = str(output)
        low = text.lower()
        rc = getattr(output, "returncode", None)
        n_failed = n_passed = 0
        m = re.search(r"(\d+) failed", low)
        if m:
            n_failed = int(m.group(1))
        m = re.search(r"(\d+) passed", low)
        if m:
            n_passed = int(m.group(1))
        failed_markers = (
            "failed",
            "errors=",
            "failures=",
            "traceback",
            "no such file",
            "command not found",
            "collected 0 items",
            " fail",
            "assertionerror",
        )
        looks_failed = (
            n_failed > 0 or (rc not in (0, None)) or any(mk in low for mk in failed_markers)
        )
        looks_passed = (n_passed > 0 and n_failed == 0) or "pass" in low or rc == 0
        passed = bool(looks_passed and not looks_failed)
        return {
            "passed": passed,
            "n_passed": n_passed,
            "n_failed": n_failed,
            "returncode": rc,
            "preview": text[-500:],
        }

    async def verify(self, check_cmd: str) -> str:
        """Run a real acceptance check and record the outcome for the return gate.

        THIS is the only way to satisfy the gate that guards ``return_result``.
        Terminal-bench tasks are graded by held-out tests uploaded only AFTER you
        stop — you cannot run them. So you must run an acceptance check YOU can
        run now that proves the task is actually done, e.g.:

          - the build succeeds: ``verify("cd /app/MdfLib && cmake --build build")``
          - a self-test passes: ``verify("bash run-tests.sh")`` / ``verify("pytest -q")``
          - the required artifacts exist AND conform: e.g. a one-off python check
            that loads every expected output file and validates it against the
            spec in the instruction, exiting non-zero if anything is missing or
            malformed.

        A check that merely prints "PASS" without actually exercising the work
        does NOT count — make it a real check whose exit code reflects reality.
        The gate opens only when your most recent ``verify()`` shows success.

        Args:
            check_cmd: Shell command that runs your acceptance check. Should exit
                non-zero on failure (pytest / make / a python -c assertion / etc.).

        Returns:
            A short status line (passed / failed) — also printed.
        """
        output = await self.terminal.execute(check_cmd)
        outcome = self._parse_check_outcome(output)
        outcome["cmd"] = check_cmd
        self._last_verify = outcome
        verdict = "PASSED" if outcome["passed"] else "FAILED"
        msg = (
            f"verify [{verdict}]  cmd={check_cmd!r}  rc={outcome['returncode']} "
            f"passed={outcome['n_passed']} failed={outcome['n_failed']}"
        )
        print(msg)
        print(output)
        return msg

    def _verification_gate(self) -> str | None:
        """Return a nudge if the agent must NOT stop yet, else None.

        TB has no git worktree to diff and the graded tests are verifier-only, so
        the single enforceable invariant is: the agent ran a real acceptance
        check via ``self.verify()`` this session AND it passed. A premature stop
        is bounced with a concrete reason instead of recording an unverified
        "done".
        """
        if self._last_verify is None:
            return (
                "STOP REJECTED: you never ran an acceptance check this session. "
                "TB grades with held-out tests you can't see — so prove the task "
                "is done with a check YOU can run: build succeeds / a self-test "
                "passes / a script that loads every required output and validates "
                "it against the instruction's spec (exit non-zero on any miss). "
                "Run it via `await self.verify('<check command>')` and see it pass."
            )
        if not self._last_verify["passed"]:
            lv = self._last_verify
            return (
                f"STOP REJECTED: your last `verify()` FAILED "
                f"(rc={lv['returncode']}, cmd={lv['cmd']!r}). Fix the work until "
                "that check passes. A check that just prints PASS without "
                "exercising the real outputs does not count."
            )
        return None

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point for the Harbor evaluation framework.

        Extracts task fields, populates context blocks, and calls
        ``solve_task``.  Returns a dict with ``response``, ``success``, and
        optionally ``error``.
        """
        # Reset verification-gate state so a reused agent instance can't carry
        # a prior green verify (or exhausted bounce counter) into a new task.
        self._last_verify = None
        self._verify_bounces = 0

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

        ## Mandatory Verification (ENFORCED — you cannot return without it)
        The held-out grading tests are NOT in this container — they're uploaded
        only AFTER you stop. So you must prove the task is done with a check YOU
        can run now, through the gate:

            await self.verify("<acceptance check command>")

        The check must genuinely exercise the work and exit non-zero on failure:
          - the build succeeds (e.g. cmake/make returns 0),
          - a shipped self-test passes (run-tests.sh / pytest),
          - OR a script that loads every required output artifact and validates
            it against the instruction's spec, failing if anything is missing or
            malformed.

        A check that merely prints "PASS" without exercising the real outputs
        does NOT count. If you ``return_result(...)`` without a passing
        ``self.verify()`` this session, the gate REJECTS the return and bounces
        you back with the reason. Just verify.

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
        """Run the task behind a verification gate.

        TB grades with held-out tests uploaded only after the agent stops, so the
        dominant failure is declaring done without running any real check. The
        gate refuses a return until the agent ran a passing acceptance check this
        session via ``self.verify()``; otherwise it bounces back into the loop
        with a concrete reason (up to ``_MAX_VERIFY_BOUNCES``), then lets the
        honest unverified result stand. State (terminal, files, last verify)
        persists across bounces on ``self``.
        """
        result = None
        self._base_description = description
        for attempt in range(self._MAX_VERIFY_BOUNCES + 1):
            try:
                result = await self._solve_task(description)
            except Exception as e:
                logger.error("Error solving Terminal Bench 2 task: %s", e)
                result = None

            nudge = self._verification_gate()
            if nudge is None:
                return result

            if attempt >= self._MAX_VERIFY_BOUNCES:
                logger.warning("Verification gate exhausted bounces; returning unverified.")
                return result
            self._verify_bounces += 1
            logger.warning(
                "Verification gate bounced return (attempt %d/%d): %s",
                self._verify_bounces,
                self._MAX_VERIFY_BOUNCES,
                nudge.split(":", 1)[0],
            )
            # Surface the blocker prominently on the next pass. TB's _solve_task
            # is a pure-prompt strategy method (no opener code to branch on), so
            # we fold the nudge into the description it re-runs with.
            description = (
                f"{self._base_description}\n\n"
                f"=== RETURN BLOCKED BY VERIFICATION GATE ===\n{nudge}\n"
                "You CANNOT finish until your most recent self.verify(<check>) shows a "
                "passing real acceptance check. Resolve the blocker above first."
            )

        return result
