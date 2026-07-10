# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI Agent extending NeMo OO Agents with Bash tools and summarization.

Uses the new summarization subagent pattern from nooa.agents.
"""

from typing import Annotated

from nooa import hidden, strategy
from nooa.agentdoc import spec
from nooa.storage.markers import nosnapshot

with hidden:
    from nooa_cli.tools.pyp import (
        Pyp,
    )
    from nooa_cli.tools.repo_tools import RepoTools

    from nooa.config import CodeActConfig, PredictConfig
    from nooa.skill_registry import SkillRegistry
    from nooa.strategies import CodeActStrategy, PredictStrategy
    from nooa.tools import SkillWriting, TodoManager
    from nooa.tools.shell_tools import ShellTools
    from nooa.tools.todo import Todo

# Standard library — all visible in REPL
import asyncio  # noqa: F401
import datetime  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401

from nooa.runtime import producers  # noqa: F401
from nooa.runtime.producers import after, cron, monitor, run_job, tail  # noqa: F401

# os is used by this module (NEMO_OO_RICH_URL check) but not useful to expose to
# the agent's REPL — hide it so doc(self) / exec_globals don't advertise it.
with hidden:
    pass

# Optional third-party libraries — visible in REPL (use np, pd, px, go directly)
try:
    import numpy as np  # noqa: F401  # type: ignore[import-untyped]
except ImportError:
    pass

try:
    import pandas as pd  # noqa: F401  # type: ignore[import-untyped]
except ImportError:
    pass

try:
    import plotly.express as px  # noqa: F401  # type: ignore[import-untyped]
    import plotly.graph_objects as go  # noqa: F401  # type: ignore[import-untyped]
    from plotly.subplots import make_subplots  # noqa: F401  # type: ignore[import-untyped]
except ImportError:
    pass

try:
    import scipy  # noqa: F401  # type: ignore[import-untyped]
except ImportError:
    pass

try:
    import sklearn  # noqa: F401  # type: ignore[import-untyped]
except ImportError:
    pass

with hidden:
    from nooa.unifiedllm import FakeLLMClient, UnifiedLLM

    from .config import AgentConfig, SummarizationConfig
    from .doer import DoerAgent

with hidden:
    from .models import (
        BrainstormResult,
        DiagnosisResult,
        Intent,
        Plan,
        ReviewResult,
        StepResult,
        VerificationResult,
    )


# Interactive-agent base machinery now lives in core (nooa.interactive).
# Re-exported here so existing ``nooa_tui.tui.agent`` imports keep working.
from nooa.interactive import (  # noqa: F401
    AgentVars,
    RespondKind,
    RespondReason,
    RespondResult,
    _summarizer_budget,
    apply_model_limits,
    install_summarizer,
)
from nooa.interactive import (
    InteractiveAgent as _InteractiveAgent,
)
from nooa.interactive import (
    SummarizationConfig as SummarizationConfig,  # noqa: F401, PLC0414
)

# Default LLM for class definition (overridden at instantiation)
with hidden:
    try:
        from nooa.unifiedllm import get_llm_client

        from .config import DEFAULT_MODEL

        _DEFAULT_LLM = get_llm_client(DEFAULT_MODEL)
    except Exception:
        from nooa.unifiedllm import FakeLLMClient

        _DEFAULT_LLM = FakeLLMClient()


# Orchestrator mode (multi-phase workflow via classify/brainstorm/plan/verify)
# was removed when respond() became a forever-loop. The phase methods
# (classify_intent, _legacy_brainstorm, write_plan, etc.) are still defined
# on TUIAgent and can be invoked by the LLM via CodeAct — they just no longer
# drive a state machine from respond().


@hidden
class BaseTUIAgent(_InteractiveAgent, llm=_DEFAULT_LLM):
    """Base class for agents that work with the NeMo OO Agents TUI.

    A thin TUI layer over :class:`nooa.interactive.InteractiveAgent` (which
    provides the input queues, ``self.v`` persistent vars, ``handle()`` turn
    protocol, and summarization). This subclass pins the TUI event type for
    ``message()`` (the session store and explorers match on the exact
    ``TUIAgentMessage`` name) and installs the TUI's Python tools.
    """

    def __init__(self, llm=None, **kwargs):
        super().__init__(llm=llm or _DEFAULT_LLM, **kwargs)
        self.pyp = Pyp()

    def message(self, text: str, *, echo: bool = False) -> None:
        """Send a Markdown message to the user.

        Each call renders as an independent block — so every call must be a
        complete, self-contained Markdown document.  In particular, never split
        a table across calls: the header row and all data rows must be in the
        same ``message()`` call, otherwise the table will not render correctly.

        Args:
            text: Markdown content to send.
            echo: If True, also ``print()`` the text so the LLM can see it
                in the execution output. By default the LLM does NOT see the
                content of ``message()`` calls — only the user does. Use
                ``echo=True`` when you need to reference the sent content in
                subsequent cells.
        """
        from .tui_events import TUIAgentMessage

        event = TUIAgentMessage(content=str(text))
        tag = self.event_manager.add(event)
        if self._render_message is not None:
            try:
                self._render_message(text, event_id=str(event.id), tags={str(tag)})
            except TypeError:
                self._render_message(text)
        if echo:
            print(text)


class TUIAgent(BaseTUIAgent, llm=_DEFAULT_LLM):  # type: ignore[call-arg]
    """You are NeMo OO Agents, a software-development assistant running in a terminal.

    # How to respond

    Direct, terse, no filler. No "Great question!" preambles, no "Let me know
    if you need anything else!" postambles. Match the scale of the request —
    a one-line question gets a one-line answer; a feature gets a feature, not
    an essay about the feature. Disagree when you have evidence; don't agree
    to keep the peace.

    # How to do the work

    - **Match the scope.** If the user asks for X, deliver X. Don't refactor
      unrelated code. Don't add speculative error handling. Don't design for
      hypothetical future requirements. A bug fix doesn't need surrounding
      cleanup; a one-shot operation doesn't need a helper.

    - **Understand before fixing.** Read the code and reproduce the failure
      mode before editing. For bugs, find the root cause — don't paper over
      symptoms or wrap things in try/except to swallow the real error.

    - **Evidence before claiming done.** Run tests / scripts / checks before
      saying the work is finished. "It should work" is not evidence. If you
      can't test something (e.g. a UI interaction), say so explicitly rather
      than claim success.

    - **Edit over create.** Prefer editing existing files to creating new
      ones. Don't fork a new variant of a function when modifying it would do.

    - **Comments only when the WHY is non-obvious.** A hidden constraint, a
      subtle invariant, a workaround for a specific bug, behavior that would
      surprise a reader. Never write comments that explain WHAT the code does
      — well-named identifiers already do that.

    - **No backwards-compat cruft** in local code. Don't add "_removed_X"
      shims, don't keep dead names for "just in case", don't version-gate
      tiny refactors.

    # Reflection — capture what you learned

    After a non-trivial task (anything that took real thought, not just
    a one-liner), take a minute to persist the reusable part before
    ending your turn. Two shapes:

    - **New pattern** — if you wrote logic that will help next time,
      save it as a ``Skill`` subclass in a new library via ``self.libs``.
      ``__init__.py`` exports the ``Skill``; the library manager
      attaches it as ``agent.<lib_name>`` automatically next session.
    - **Existing skill needs an update** — if the task surfaced a
      missing method, a clearer docstring, a better default, edit the
      skill's library (edit with ``self.shell.replace(...)`` then ``self.libs.reload(lib_name)``).

    Don't over-do it. A task that was just "run these tests and report"
    doesn't need a new skill. But if you just invented a non-obvious way
    to slice the data or coerce a third-party API, that's worth keeping.
    Libraries are how ``self`` gets smarter over time.

    # When to ask

    If the request is ambiguous, send a clarifying question via
    ``self.message(...)`` and end the turn with
    ``return_result(RespondReason.NEED_INPUT, explanation="need clarification from the user before proceeding")``.
    The dispatcher blocks on the next user message and re-enters
    ``handle()`` with their answer.

    Don't guess at interpretation and produce output the user has to reject.

    # Destructive actions

    Confirm before: ``rm -rf``, ``git push --force``, dropping DB tables,
    killing processes, ``git reset --hard``, pushing to ``main``, sending
    messages (Slack/GitHub/etc.), modifying files the user didn't mention.
    Cheap to confirm, expensive to undo.

    # TODOs

    Use ``self.todo`` for work with three or more distinct steps, or when
    the user will want to watch progress. Don't use it for trivial one-step
    tasks.

    **Check items off the moment they're done — every time, without
    exception.** The instant a step finishes successfully, and *before*
    starting the next one, call ``self.todo.done(t.id)``. Do not batch
    completions at the end of the turn. Do not wait until "everything is
    done" to catch up. The user watches ``<todo_status>`` update live —
    stale in-progress items make it look like you're stuck or lost, even
    when you're making progress.

    If a step fails, leave it open. Only mark it done after you've actually
    fixed the failure. Partial credit is a lie you're telling yourself.

    Inline execution:
        t = self.todo.add("Explore the codebase")
        result = await self.do_it(t)

    Delegate to a Doer subagent (isolates context, good for complex items):
        t = self.todo.add("Run the full test suite and triage failures")
        result = await self.make_doer().execute(t)

    Parallel when todos are independent (each doer gets its own context):
        t1 = self.todo.add("Fix module A")
        t2 = self.todo.add("Fix module B")
        t3 = self.todo.add("Run tests", deps=[t1.id, t2.id])
        r1, r2 = await asyncio.gather(
            self.make_doer().execute(t1),
            self.make_doer().execute(t2),
        )

    Background doers (launch and stay responsive to the user):
        t = self.todo.add("Run full test suite")
        ch = self.queue_manager.queue("doer_results")
        self.queue_manager.spawn(
            self.make_doer().execute(t),
            channel="doer_results",
        )
        # → return immediately; result arrives as a notification
        #   on the "doer_results" channel in a future turn
        return_result(RespondReason.WAIT, explanation="waiting for a background job or queue event")

    The ``<todo_status>`` context block shows current progress every turn.

    # Long-running tasks

    For commands that take more than ~10s, **spawn them** instead of
    blocking the turn with ``self.shell.run()``:

        ch = self.queue_manager.queue("ci")
        h = self.queue_manager.spawn(
            self.producers.monitor("make test"),
            channel="ci",
            buffer=100,  # ring buffer: last 100 lines
        )
        # → notifications arrive as ("ci", <line>) in subsequent turns
        # → h.values gives the last 100 lines at any time
        # → h.state shows "running"/"done"/"failed"/"cancelled"
        # → await self.queue_manager.shutdown() cancels all

    Use ``self.shell.run()`` for quick commands (<10s). Use ``spawn()``
    for CI pipelines, long builds, monitoring, and anything you want
    to run concurrently while staying responsive to the user.

    # Execution model

    - Run **one** thing at a time and observe the result before the next
      step. Don't build giant blocks that stop at the first error.
    - **Start each cell with a ``# Doing X next`` comment.** One short
      line at the top of every ``execute_python`` describing the next
      step. The TUI surfaces these so the user can follow the work
      live; cells without one read as opaque to anyone watching.
    - **Where state lives — the persistence ladder, in increasing
      lifetime:**
        1. **REPL locals** — cleared between turns. Names defined in
           one ``execute_python`` are gone in the next.
        2. **``self.v.k = v``** — snapshot-backed, survives turns AND
           sessions. Use for long-lived agent state (current plan,
           cursor into a long task, learned facts).
        3. **``self.todo.<id>.v.k = v``** — same as ``self.v`` but
           scoped to one todo. Use when the variable belongs to that
           specific work item.
    - **Use ``self.v`` aggressively** — store working state (file
      paths, plan summaries, handles, progress cursors) so you can
      resume coherently if context is summarized or the session
      restarts. Anything you'd want to remember next turn goes in
      ``self.v``.
    - Use ``print()`` / ``pprint()`` to inspect intermediate state.
    - No ``import`` — every module you need is pre-loaded (np, pd, json,
      asyncio, etc.). Check the execution_context for what's available.
    - **Ending a turn.** Only call
      ``return_result(RespondReason.<...>, explanation="...")`` when you are completely
      done with all work or you need user input. ``explanation`` is required and must be non-empty.
      The TUI records/renders it as the reason you stopped, so make it specific:
        * ``kind="DONE"`` — use when the request is complete. State what was
          completed or verified.
        * ``kind="NEED_INPUT"`` — use after asking a follow-up or when blocked
          on human input. State what input you need and why.
        * ``kind="WAIT"`` — use when background jobs are running and you
          want the dispatcher to wake you on the next queue event. State which
          job/queue/event you are waiting for and why.


    # Communication mechanics

    ``self.message(text)`` — Markdown to the user, rendered when the
    current code cell finishes. Use for final answers and for status
    the user should see. Call it multiple times per turn as needed.
    **The LLM does NOT see the content** of ``message()`` calls unless
    you pass ``echo=True``, which also ``print()``s the text so it
    appears in your execution output.

    ``print()`` / ``pprint()`` — debugging output visible only to you
    (the agent). The user does **not** see print output. Use it to
    inspect intermediate state; use ``self.message()`` to talk to the
    user.

    Python comments in ``execute_python`` — your internal thinking. The user
    doesn't see them but the next turn does. Use sparingly: a one-liner when
    a decision is non-obvious.

    The outer dispatcher calls ``handle(notification)`` once per turn.
    ``notification`` is a ``dict[str, list]`` — channel name → items.
    """

    _config: Annotated[AgentConfig, hidden, nosnapshot]
    shell: Annotated[ShellTools, nosnapshot]
    repo: Annotated[RepoTools, nosnapshot]
    libs: Annotated[SkillWriting, nosnapshot]
    todo: TodoManager
    skills: Annotated[SkillRegistry, nosnapshot]
    _skills_dirs: Annotated[list, hidden, nosnapshot]
    _summarizers: Annotated[list, hidden, nosnapshot]

    def __init__(
        self,
        llm: UnifiedLLM | None = None,
        config: AgentConfig | None = None,
        **kwargs,
    ) -> None:
        """Initialize TUI agent.

        Args:
            llm: LLM client to use
            config: Agent behavior configuration (summarization, working_dir, etc.)
            **kwargs: Additional arguments passed to Agent
        """
        super().__init__(llm=llm, **kwargs)

        config = config or AgentConfig()

        # Store config for later access
        self._config = config

        self._skills_dirs: list = []  # set by bootstrap with CLI --skills-dir + entry points

        from nooa.paths import get_project_dir

        _project_dir = get_project_dir()
        self._install_python_tools(config.working_dir)
        self.libs = SkillWriting(self, path=_project_dir / "libs")
        self.todo = TodoManager()

        # Skill registry: register + activate skills
        self.skills = SkillRegistry(self)
        self.skills.register("nemo.shell", self.shell)
        self.skills.register("nemo.repo", self.repo)
        self.skills.register("nemo.todo", self.todo)
        self.skills.register("nemo.libwriting", self.libs)
        builtin_skills = [
            n for n in self.skills.discovered() if n.startswith("nemo.") and n != "nemo.memory"
        ]
        self.skills.activate(builtin_skills)

        # Render Python tool docs together so ShellTools/RepoTools stay paired in context.
        from nooa import Context

        self.context["python_tools"] = Context(expr="doc(RepoTools, ShellTools)", prefix=True)

        # Skills register their own context blocks via context_block class attr

        # Expose context and events to the LLM
        spec(self, "context", hidden=False)
        spec(self, "events", hidden=False)

        # Show context-window usage to the LLM every turn (lets the agent
        # decide when to call /compact). Value is from the PREVIOUS turn's
        # render, so the very first respond() call sees an empty string.
        self.context["context_usage"] = Context(
            expr="self.context_stats.format() if self.context_stats else ''",
        )

        # Install summarizer after agent is initialized
        if config.summarization.policy != "none":
            install_summarizer(config.summarization, agent=self)

    def _install_python_tools(self, cwd: str) -> None:
        """Install shell/repo tools rooted at the same working directory."""
        self.shell = ShellTools(cwd=cwd)
        self.repo = RepoTools(root=cwd, session=self.shell._session)

    @hidden
    def get_summarization_status(self) -> dict:
        """Get current summarization status.

        Returns:
            Dict with summarization info (active_events, token usage, etc.)
        """
        active_tags = self.event_manager.keys()
        summary_tags = [t for t in active_tags if ".." in t]

        # Get token info from summarizer if available
        current_tokens = 0
        max_tokens = 0
        preserve_recent = 0
        summarizers = getattr(self, "_summarizers", [])
        if summarizers:
            # Use first summarizer for status (typically only one)
            summarizer = summarizers[0]
            stats = self.context_stats
            current_tokens = stats.total_tokens if stats else None
            current_tokens = current_tokens or 0
            max_tokens = getattr(summarizer, "max_tokens", 0)
            preserve_recent = getattr(summarizer, "preserve_recent", 0)

        return {
            "active_events": len(active_tags),
            "summary_count": len(summary_tags),
            "summary_tags": summary_tags,
            "has_summarizer": len(summarizers) > 0,
            "policy": self._config.summarization.policy,
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "preserve_recent": preserve_recent,
        }

    def make_doer(self) -> "DoerAgent":
        """Build a fresh ``DoerAgent`` wired with this agent's tools and skills.

        Each doer has its own context window, so the parent's context doesn't
        fill up with the doer's scratch work. Call ``.execute(todo)`` to
        run a single todo item to completion.
        """
        memory_skill = getattr(self, "memory", None)
        memory_config = getattr(memory_skill, "_config", None)
        return DoerAgent(
            llm=self._llm,
            cwd=self.shell.cwd,
            repo=self.repo,
            todo=self.todo,
            skills_dirs=self._skills_dirs,
            shell_cls=type(
                self.shell
            ),  # propagate the parent's current shell variant (/swap-shell)
            memory_config=memory_config,
        )

    @strategy(CodeActStrategy())
    async def do_it(self, todo: Todo) -> str:
        """Execute a single todo item and return a summary of what was done.

        Todo: [{todo.id}] {todo.title}
        Notes: {todo.notes}

        Instructions:
        1. Read the todo title and notes carefully.
        2. Execute the work described using self.shell (run for shell commands,
           read to view a file/region, replace to edit at a match or unique
           string, write_file to create/overwrite) and self.repo (symbols, refs).
        3. When done, update the todo with what you learned:
           self.todo.update("{todo.id}", notes="what you did and found")
        4. Mark it complete: self.todo.done("{todo.id}")
        5. Return a concise summary of what you did and the outcome.

        Focus only on this one item. Do NOT work on other todos.
        """
        ...

    @hidden
    @strategy(PredictStrategy(PredictConfig(output_serialization="tool_call")))
    async def classify_intent(self, user_message: str) -> Intent:
        """Classify the user's message into a task type.

        Message: {user_message}

        Determine:
        - task_type: "question" (asking for info or a simple request that needs no planning),
          "feature" (build something new), "bugfix" (something is broken),
          "refactor" (restructure existing code)
        - summary: One-sentence description of what the user wants
        """
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=30)))
    async def answer_question(self, user_message: str) -> None:
        """Answer the user's question or handle their simple request.

        User message: {user_message}

        Use tools to gather information.
        Use self.message() to respond to the user with a clear, helpful answer.
        Use Markdown formatting for readability.
        """
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=15)))
    async def _legacy_brainstorm(self, request: str) -> BrainstormResult:
        """Explore requirements for: {request}

        Your job is to understand what to build BEFORE writing any code.

        Ask the user clarifying questions ONE AT A TIME about:
        - Scope: What exactly should this do? What should it NOT do?
        - Constraints: Performance requirements? Compatibility? Dependencies?
        - Location: Where should this code live? What existing patterns to follow?
        - Interface: What should the API/interface look like?

        Use self.message() to ask each question.
        Use tools to explore the codebase for context.

        When you have enough understanding, call return_result() with complete=True
        and all the decisions captured.

        If you asked the user a question and need their response, call return_result()
        with complete=False and pending_question set to the question you asked.

        Do NOT write implementation code. Do NOT skip asking questions."""
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))
    async def write_plan(self, spec: str) -> Plan:
        """Create an implementation plan.

        Brainstorm results: {spec}

        Create a numbered list of implementation steps. Each step should be:
        - Small enough to implement and test independently
        - Ordered by dependency (foundations first)
        - Specific about which files to create/modify

        Use tools to check the existing codebase for patterns.
        Present the plan to the user via self.message() before returning it."""
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100)))
    async def implement_step(self, step: str) -> StepResult:
        """Implement this plan step using test-driven development.

        Step: {step}

        Follow this exact sequence:
        1. RED: Write a failing test for this step's expected behavior
           - Run the test, confirm it fails for the RIGHT reason
        2. GREEN: Write the minimum implementation to make the test pass
           - Run the test, confirm it passes
        3. REFACTOR: Clean up if needed, run tests again to confirm nothing broke

        Use self.bash to run tests: await self.bash.run("pytest path/to/test -v")
        Use self.files to read and write code.

        Do NOT skip the RED phase. Do NOT write implementation before the test.
        If the test passes immediately, your test is wrong — fix the test first."""
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=30)))
    async def debug_issue(self, description: str) -> DiagnosisResult:
        """Debug: {description}

        Follow this exact sequence:
        1. REPRODUCE: Find a way to trigger the issue. Run a test or command
           that demonstrates the bug. If you cannot reproduce, ask the user.
        2. HYPOTHESIZE: Based on the reproduction, form 2-3 hypotheses about
           the root cause. State them explicitly in `#` comments.
        3. INVESTIGATE: For each hypothesis, run targeted investigation
           (read code, add logging, inspect state). Eliminate hypotheses.
        4. FIX: Once root cause is confirmed, implement the fix.
        5. VERIFY: Run the reproduction again. Confirm the bug is fixed.

        Do NOT jump to a fix without reproducing first.
        Do NOT guess — investigate systematically."""
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))
    async def verify_work(self) -> VerificationResult:
        """Verify that recent changes work correctly.

        Run ALL of these checks:
        1. Run the test suite: await self.bash.run("pytest --tb=short -q")
        2. Check for lint/type errors if applicable
        3. Review the git diff: await self.bash.run("git diff")

        Report what you found. Do NOT claim success without evidence.
        If tests fail, report which ones and why."""
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))
    async def review_changes(self, plan: str) -> ReviewResult:
        """Review the implementation against the plan.

        Plan: {plan}

        Check:
        1. Completeness: Are all plan steps addressed in the diff?
        2. Correctness: Does the implementation match what was planned?
        3. Quality: Any obvious issues, missed edge cases, or dead code?

        Use self.bash.run("git diff") to see what changed.
        Report your findings honestly."""
        ...

    # handle() is inherited from BaseTUIAgent and runs once per inbound
    # notification. See the BaseTUIAgent.handle docstring for the turn pattern.
