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
import re
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

    ### Phase 3: Verify (DO NOT SKIP — this is ENFORCED)

    3a. **Run the failing tests through the gate**: call
        `await self.verify("<test command>")` with the REAL test command
        (pytest / the repo's runtests.py / unittest — whatever runs the
        failing tests). This is the ONLY way to satisfy the return gate: a
        hand-rolled script that prints "PASSED" does NOT count. You cannot
        finish until your most recent `verify()` shows a green run.
    3b. **Run related tests**: Check for regressions (also via `verify()`).
    3c. **Review diff**: `await self.shell.run("git diff HEAD")` — no debug prints,
        no spurious whitespace changes, no unnecessary modifications.

    If you `return_result(...)` without (a) edits in the worktree and (b) a
    passing `verify()` this session, the gate REJECTS the return and bounces
    you back with the reason. Don't waste turns testing that — just verify.

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

    # Max times solve_task() will bounce a premature return back into the loop
    # before giving up and letting the (honest) failure stand.
    _MAX_VERIFY_BOUNCES = 3

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        self.shell = _make_shell("/testbed")
        self.todo = TodoManager()
        self.problem_statement = ""
        # Verification-gate state: the LAST test run the agent did via
        # ``self.verify()`` this session, and how many times we have bounced a
        # premature return. ``last_verify`` is None until the agent runs the
        # tests through the gate — a hand-rolled "print(\'PASSED\')" script does
        # NOT set it, so the model cannot fake its way past the gate.
        self._last_verify: dict[str, Any] | None = None
        self._verify_bounces = 0
        self._pending_nudge: str | None = None
        from nemo_oo_agents import Context

        self.context_manager["self.shell"] = Context(doc(type(self.shell)), prefix=True)

    @hidden
    def _parse_test_outcome(self, result: Any) -> dict[str, Any]:
        """Best-effort pass/fail parse of a test run's output.

        Recognizes pytest, Django's runtests.py, and unittest summaries. Errs
        toward ``passed=False`` when ambiguous — the gate should only open on a
        clearly-green run, never on a maybe.
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
        # Django/unittest style: "FAILED (failures=2, errors=1)" / "OK"
        failed_markers = (
            "failed (",
            "errors=",
            "failures=",
            " no tests ran",
            "collected 0 items",
        )
        rc = getattr(result, "returncode", None)
        looks_failed = n_failed > 0 or any(mk in low for mk in failed_markers)
        looks_passed = (n_passed > 0 and n_failed == 0) or low.rstrip().endswith("ok")
        passed = bool(looks_passed and not looks_failed and (rc in (0, None)))
        return {
            "passed": passed,
            "n_passed": n_passed,
            "n_failed": n_failed,
            "returncode": rc,
            "preview": text[-500:],
        }

    async def verify(self, test_cmd: str) -> str:
        """Run the failing test(s) and record the outcome for the return gate.

        THIS is the only way to satisfy the "did you actually verify?" gate that
        guards ``return_result``. A hand-rolled ``python -c '...print("PASSED")'``
        script does NOT count — you must run the real test command (pytest, the
        repo's runtests.py, etc.) through here. The gate opens only when the most
        recent ``verify()`` shows a green run AND the worktree carries edits.

        Args:
            test_cmd: The shell command that runs the task's failing tests
                (e.g. ``pytest path/to/test_x.py::test_y`` or
                ``./tests/runtests.py app.tests.Foo``).

        Returns:
            A short status line (passed / failed + counts) — also printed.
        """
        result = await self.shell.run(test_cmd, timeout=600)
        outcome = self._parse_test_outcome(result)
        outcome["cmd"] = test_cmd
        self._last_verify = outcome
        verdict = "PASSED" if outcome["passed"] else "FAILED"
        msg = (
            f"verify [{verdict}]  cmd={test_cmd!r}  "
            f"passed={outcome['n_passed']} failed={outcome['n_failed']} rc={outcome['returncode']}"
        )
        print(msg)
        print(result)
        return msg

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
        from nemo_oo_agents import Context

        self.context_manager["self.shell"] = Context(doc(type(self.shell)), prefix=True)
        self.todo.clear()
        self._last_verify = None
        self._verify_bounces = 0
        self._pending_nudge = None

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
        # On a verification bounce we re-enter here with a pending nudge. Don't
        # re-run the explore opener (the agent already explored, and re-showing
        # all-done todos just invites another instant return). Surface the nudge
        # as the FIRST and most prominent thing, with the current diff + verify
        # state, so the agent acts on it instead of re-declaring done.
        if self._pending_nudge:
            diff = (await self.shell.run("git diff HEAD")).text.strip()
            print("=== RETURN BLOCKED BY VERIFICATION GATE ===")
            print(self._pending_nudge)
            print("\n=== Worktree edits present:", bool(diff), "===")
            lv = self._last_verify
            print(
                "=== Last verify:",
                "none yet"
                if lv is None
                else f"{'PASSED' if lv['passed'] else 'FAILED'} "
                f"(passed={lv['n_passed']} failed={lv['n_failed']}, cmd={lv['cmd']!r})",
                "===",
            )
            print("\n=== Todo Status ===")
            print(self.todo.status())
            print(
                "\nResolve the blocker above. You CANNOT finish until your most "
                "recent self.verify(<cmd>) shows a green run on real tests."
            )
            self._pending_nudge = None
            ...
            return "done"

        # Start with Phase 1a: explore the repo structure
        r = await self.shell.run("ls -la")
        print("=== Repo Structure ===")
        print(r)
        print("\n=== Todo Status ===")
        print(self.todo.status())
        ...

    @hidden
    def _reopen_verify_todos(self) -> None:
        """Reopen the Phase-3 verify todos after a bounce.

        The agent re-declares done partly because todo_status reads all-complete.
        Reopening the verify phases makes the pending work visible so the next
        pass doesn't just re-confirm a finished checklist.
        """
        for t in self.todo.list_todos():
            title = getattr(t, "title", "")
            if "Phase 3a" in title or "Phase 3b" in title:
                try:
                    self.todo.reopen(t.id)
                except Exception:  # noqa: BLE001
                    logger.debug("Could not reopen verify todo %s (%r)", t.id, title, exc_info=True)

    async def _verification_gate(self) -> str | None:
        """Return a nudge string if the agent must NOT stop yet, else None.

        Three things must hold before a ``return_result`` is honored:
          1. the worktree carries edits (the harness grades /testbed in place);
          2. the agent ran the failing tests THIS session via ``self.verify()``;
          3. that most-recent verify showed a green run.

        Each failing condition yields a concrete, actionable nudge — the agent
        is bounced back into the loop with exactly one reason it can't stop,
        rather than being allowed to record a bogus "done". A passing manual
        script never sets ``_last_verify``, so it cannot open the gate.
        """
        diff = (await self.shell.run("git diff HEAD")).text.strip()
        if not diff:
            return (
                "STOP REJECTED: your worktree has NO edits (`git diff HEAD` is empty). "
                "The harness grades /testbed in place — your file changes ARE the "
                "submission. Make the actual fix, then verify it, then finish."
            )
        if self._last_verify is None:
            return (
                "STOP REJECTED: you never ran the failing tests this session. A fix you "
                "haven't run is not a fix. Run the task's failing test(s) via "
                '`await self.verify("<test command>")` (real pytest / runtests.py — '
                "NOT a hand-rolled script that prints PASSED) and see them pass first."
            )
        if not self._last_verify["passed"]:
            lv = self._last_verify
            return (
                f"STOP REJECTED: your last `verify()` FAILED "
                f"(passed={lv['n_passed']} failed={lv['n_failed']}, cmd={lv['cmd']!r}). "
                "Fix the implementation until that exact command passes. A manual "
                "script printing success does not count — make the real tests green."
            )
        return None

    async def solve_task(self, description: str, response_format: str = "") -> str:
        """Public wrapper: run the workflow behind a verification gate.

        The harbor verifier grades the working tree in place — file edits in
        /testbed ARE the submission. The dominant failure mode is the agent
        declaring ``done`` without ever running the failing tests and seeing
        them pass. So we gate the return: when the agent stops, we check that
        (a) the worktree carries edits and (b) the agent verified a green test
        run this session via ``self.verify()``. If not, we bounce it back into
        the loop with a concrete reason — up to ``_MAX_VERIFY_BOUNCES`` times —
        instead of accepting an unverified submission. State (shell, /testbed
        edits, todos, last verify) persists across bounces on ``self``.

        Non-diff tasks keep the old pass-through behavior.
        """
        result = None
        desc = description
        for attempt in range(self._MAX_VERIFY_BOUNCES + 1):
            try:
                result = await self._solve_task(desc, response_format)
            except Exception as e:
                logger.error("Error solving task: %s", e)
                result = None

            if response_format != "diff":
                return str(result) if result is not None else ""

            nudge = await self._verification_gate()
            if nudge is None:
                return str(result) if result is not None else "done"

            if attempt >= self._MAX_VERIFY_BOUNCES:
                # Out of bounces — let the (honest) unverified result stand.
                logger.warning("Verification gate exhausted bounces; returning unverified.")
                return str(result) if result is not None else "done"
            self._verify_bounces += 1
            logger.warning(
                "Verification gate bounced return (attempt %d/%d): %s",
                self._verify_bounces,
                self._MAX_VERIFY_BOUNCES,
                nudge.split(":", 1)[0],
            )
            # Re-enter the loop. Two things make the nudge actually land instead
            # of the agent re-declaring done off already-complete todos:
            #   1. ``_pending_nudge`` makes _solve_task skip the explore opener
            #      and surface the blocker as the first/only thing it sees;
            #   2. reopening the verify todos so todo_status shows pending work
            #      (the agent re-returned because everything read "done").
            self._pending_nudge = nudge
            self._reopen_verify_todos()
            desc = description

        return str(result) if result is not None else "done"
