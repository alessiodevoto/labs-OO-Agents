# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI Agent extending NeMo OO Agents with Bash tools and summarization.

Uses the new summarization subagent pattern from nemo_oo_agents.agents.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from nemo_oo_agents import hidden, strategy
from nemo_oo_agents.agentdoc import doc, spec
from nemo_oo_agents.storage.markers import nosnapshot

with hidden:
    from collections.abc import Callable

    from nemo_oo_agents import Agent, InputQueue
    from nemo_oo_agents.agents import TokenBudgetSummarizer
    from nemo_oo_agents.config import CodeActConfig
    from nemo_oo_agents.runtime.input_queue import OutputQueue
    from nemo_oo_agents.strategies import CodeActStrategy, PredictStrategy
    from nemo_oo_agents.tools import BashTool, FileTool, LibraryWriting, TodoManager
    from nemo_oo_agents.tools.web_publisher import WebPublisher

# Standard library — all visible in REPL
import json  # noqa: F401
import re  # noqa: F401

# os is used by this module (NEMO_RICH_URL check) but not useful to expose to
# the agent's REPL — hide it so doc(self) / exec_globals don't advertise it.
with hidden:
    import os

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
    from unifiedllm import FakeLLMClient, UnifiedLLM

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


RespondKind = Literal["GET_USER_INPUT", "WAIT", "STOP"]


class RespondResult(BaseModel):
    """Return value for ``respond()`` — signals what the outer loop should do next.

    Fields:

    - ``kind`` — one of:
        * ``"GET_USER_INPUT"`` — dispatcher awaits ``agent.user_messages.get()``
          and re-enters ``respond(notification)`` with the new message.
        * ``"WAIT"`` — dispatcher races every ``InputQueue`` declared on
          the agent (``wait_for_any``) and re-enters with the first arrival.
        * ``"STOP"`` — end the session; dispatcher exits without re-entering.

    - ``persist`` — a dict of ``name -> value`` to carry into the next
      ``respond()`` turn. The dispatcher passes this dict in as the
      ``restored`` keyword argument. Omit or pass ``{}`` to clear.

    Build from within the LLM's ``execute_python`` code::

        return_result(RespondResult(
            kind="GET_USER_INPUT",
            persist={"plan": plan, "cursor": cursor},
        ))
    """

    kind: RespondKind = Field(description="What the outer dispatcher should do next")
    persist: dict[str, Any] = Field(
        default_factory=dict,
        description="Variables to carry into the next respond() turn (name -> value)",
    )

    model_config = {"arbitrary_types_allowed": True}


# Default LLM for class definition (overridden at instantiation)
with hidden:
    try:
        from unifiedllm import get_llm_client

        from .config import DEFAULT_MODEL

        _DEFAULT_LLM = get_llm_client(DEFAULT_MODEL)
    except Exception:
        from unifiedllm import FakeLLMClient

        _DEFAULT_LLM = FakeLLMClient()


# Summarizer trigger as a fraction of the LLM's context window. This is the
# ONLY budget the TUI manages — event-pile truncation is enforced at the
# runtime level (see ActorRuntime._build_messages) and adapts to whichever
# LLM is actually resolved for each call (including per-call overrides).
_SUMMARIZER_BUDGET_PCT = 0.8


def _summarizer_budget(llm: "UnifiedLLM") -> int:
    """Resolve the summarizer trigger from the LLM's context window.

    Falls back to 100K when the LLM doesn't expose ``context_window`` so
    we still have a functional threshold.
    """
    cw = getattr(llm, "context_window", None)
    return int(cw * _SUMMARIZER_BUDGET_PCT) if cw else 100_000


def apply_model_limits(agent: Agent) -> None:
    """Sync the summarizer trigger against ``agent._llm.context_window``.

    Call after a model switch so the summarizer threshold moves with the
    new context window. Runtime-level event truncation picks up the new
    window automatically on the next ``_build_messages`` call.
    """
    from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig

    summarizer_max = _summarizer_budget(agent._llm)
    for summarizer in getattr(agent, "_summarizers", []):
        current = summarizer.config
        summarizer.config = TokenBudgetConfig(
            max_tokens=summarizer_max,
            preserve_recent=current.preserve_recent,
            target_chars=current.target_chars,
        )


def install_summarizer(config: SummarizationConfig, agent: Agent) -> None:
    """Install a summarizer on the agent based on configuration.

    Args:
        config: Summarization configuration. ``config.max_tokens=None`` (the
            default) resolves to 80% of the agent LLM's context window at
            install time, so the trigger scales with model capability.
        agent: Agent to install summarizer on (inherits LLM, attaches to history)
    """
    if config.policy == "none":
        return

    from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig

    summarizer_max = (
        config.max_tokens if config.max_tokens is not None else _summarizer_budget(agent._llm)
    )

    TokenBudgetSummarizer.install(
        agent,
        config=TokenBudgetConfig(
            max_tokens=summarizer_max,
            preserve_recent=config.preserve_recent,
            target_chars=config.target_chars,
        ),
    )


# Orchestrator mode (multi-phase workflow via classify/brainstorm/plan/verify)
# was removed when respond() became a forever-loop. The phase methods
# (classify_intent, _legacy_brainstorm, write_plan, etc.) are still defined
# on TUIAgent and can be invoked by the LLM via CodeAct — they just no longer
# drive a state machine from respond().


@hidden
class BaseTUIAgent(Agent, llm=_DEFAULT_LLM):
    """Base class for agents that work with the NeMo OO Agents TUI.

    Subclass this and implement ``respond()`` to build a custom TUI agent.
    ``message()`` and ``name_session()`` are provided for free.

    **Input queues.** Every ``BaseTUIAgent`` has a ``self.user_messages``
    queue (``InputQueue``) that the TUI feeds when the human types.
    Subclasses may declare additional queues as instance attributes for
    other producers (long-running job output, monitor streams, etc.).

    Each queue is two objects: an ``InputQueue`` (full producer +
    dispatcher API, hidden from the LLM under ``_<name>_in``) and an
    ``OutputQueue`` reader facade exposed under the public name. The
    LLM can ``await self.user_messages.get()`` to dequeue mid-turn;
    everything else (put, snapshot, qsize, etc.) is dispatcher-only.

    ``respond()`` runs *per turn* — the outer dispatcher calls it with
    the next notification ``(queue_name, item)`` and whatever
    ``restored`` dict the previous turn asked to carry over.
    """

    _render_message: Annotated[Callable[[str], None] | None, hidden, nosnapshot]
    # InputQueue (full producer/dispatcher API) is hidden — the LLM
    # has no business calling .put() / .snapshot() / etc.
    _user_messages_in: Annotated[InputQueue, hidden, nosnapshot]
    # OutputQueue (just .get() and .name) is the LLM-facing facade.
    user_messages: Annotated[OutputQueue, nosnapshot]

    def __init__(self, llm=None, **kwargs):
        super().__init__(llm=llm or _DEFAULT_LLM, **kwargs)
        self._render_message = None
        self._user_messages_in = InputQueue("user_messages", agent=self)
        self.user_messages = self._user_messages_in.reader
        if os.environ.get("NEMO_RICH_URL"):
            from nemo_oo_agents.tools.web_publisher import RichOutput

            self.event_manager.register_event_type(RichOutput)
            self.web: Annotated[WebPublisher, nosnapshot] = WebPublisher(
                event_manager=self.event_manager
            )
            self.context["web"] = doc(self.web)

    def message(self, text: str) -> None:
        """Send a Markdown message to the user.

        Each call renders as an independent block — so every call must be a
        complete, self-contained Markdown document.  In particular, never split
        a table across calls: the header row and all data rows must be in the
        same ``message()`` call, otherwise the table will not render correctly.
        """
        from .tui_events import TUIAgentMessage

        self.event_manager.add(TUIAgentMessage(content=str(text)))
        if self._render_message is not None:
            self._render_message(text)

    @hidden
    @strategy(PredictStrategy())
    async def name_session(self, user_message: str) -> str:
        """Generate an ultra-short 2-5 word session title (no punctuation, no quotes) for a conversation that starts with: {user_message}"""
        ...

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(cell_timeout=1800.0, max_iterations=100)))
    async def respond(
        self,
        notification: tuple[str, Any],
        restored: dict[str, Any] | None = None,
    ) -> "RespondResult":
        """Handle a single turn of the conversation.

        Called once per inbound notification. Unpack ``notification``
        (``(queue_name, item)``) and any ``restored`` variables from the
        previous turn, do the work, then return a ``RespondResult``
        telling the outer dispatcher what to do next.

        ## Turn anatomy

        Notification:

            queue_name, item = notification
            # queue_name is e.g. "user_messages" or "job_outputs"
            # item is the actual queued value (str for user messages,
            # whatever producers push for other queues)

        Restored state (optional — empty on the first turn):

            restored = restored or {}
            plan   = restored.get("plan")
            cursor = restored.get("cursor")

        ## Returning

        End the turn with exactly one ``return_result(RespondResult(...))``.

        - Wait for the next user message::

              return_result(RespondResult(
                  kind="GET_USER_INPUT",
                  persist={"plan": plan, "cursor": cursor},
              ))

        - Wait for ANY producer queue (user or background job)::

              return_result(RespondResult(
                  kind="WAIT",
                  persist={"job_id": job_id},
              ))

        - End the session::

              return_result(RespondResult(kind="STOP"))

        Names listed in ``persist`` become the next turn's ``restored``
        kwarg. Omit ``persist`` (or pass ``{}``) and nothing carries
        over — the next turn starts with a clean REPL.

        ## Available queues

        The dispatcher delivers the next item via ``notification``.
        You can also dequeue extra items mid-turn (without ending it)
        by awaiting the queue directly::

            extra = await self.user_messages.get()  # blocks until next message

        Recognized ``queue_name`` values:

        - ``"user_messages"`` — text from the human; ``item`` is a str.

        Subclasses may add more (e.g. ``"job_outputs"``); their
        ``Notification`` events appear in your context as they fire.
        When you return ``kind="WAIT"`` the dispatcher races every
        declared queue and re-enters with the first arrival.
        """
        ...


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
      skill's library (``self.libs.edit_file(lib_name, path, old, new)``).

    Don't over-do it. A task that was just "run these tests and report"
    doesn't need a new skill. But if you just invented a non-obvious way
    to slice the data or coerce a third-party API, that's worth keeping.
    Libraries are how ``self`` gets smarter over time.

    # When to ask

    If the request is ambiguous, send a clarifying question via
    ``self.message(...)`` and end the turn with
    ``return_result(RespondResult(kind="GET_USER_INPUT", persist=...))``.
    The dispatcher blocks on the next user message and re-enters
    ``respond()`` with their answer.

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
        result = await self.do_it(t.id, t.title, t.notes)

    Delegate to a Doer subagent (isolates context, good for complex items):
        t = self.todo.add("Run the full test suite and triage failures")
        result = await self.make_doer().execute(t.id, t.title, t.notes)

    Parallel when todos are independent (each doer gets its own context):
        t1 = self.todo.add("Fix module A")
        t2 = self.todo.add("Fix module B")
        t3 = self.todo.add("Run tests", deps=[t1.id, t2.id])
        r1, r2 = await asyncio.gather(
            self.make_doer().execute(t1.id, t1.title, t1.notes),
            self.make_doer().execute(t2.id, t2.title, t2.notes),
        )

    The ``<todo_status>`` context block shows current progress every turn.

    # Tools

    - ``self.bash`` — shell commands. Returns ``BashResult(stdout, stderr, return_code)``.
    - ``self.files`` — file I/O: ``.read()``, ``.write()``, ``.edit_file()``,
      ``.list()``, ``.find()``, ``.grep()``. Prefer these over ``self.bash``
      for file work (``cat``, ``ls``, ``find``, ``grep``, ``sed``).
    - ``self.todo`` — plan/track multi-step work. See ``doc(self.todo)``.
    - Skills — attached as attributes (e.g. ``self.wtf_pm``). **Check
      ``doc(self.<skill>)`` before using a skill** — its method signatures
      are the canonical API. Prefer a skill over ``self.bash`` whenever one
      exists for the task.

    # Execution model

    - Run **one** thing at a time and observe the result before the next
      step. Don't build giant blocks that stop at the first error.
    - **REPL variables persist within a single ``respond()`` turn but are
      cleared between turns.** A turn ends when you call
      ``return_result(RespondResult(...))``; the next turn's REPL starts
      from scratch except for whatever you passed in ``persist``.
    - **Carry state across turns via ``persist``.** Pack the names you need
      into the ``persist`` dict of the ``RespondResult`` you return, e.g.
      ``persist={"plan": plan, "cursor": cursor}``. They come back on the
      next call as the ``restored`` dict — unpack with ``.get("plan")``.
    - For long-lived plans/progress, ``self.todo.set_var(t.id, "k", v)``
      is snapshot-backed and survives across sessions, not just turns.
    - Use ``print()`` / ``pprint()`` to inspect intermediate state.
    - No ``import`` — every module you need is pre-loaded (np, pd, json,
      asyncio, etc.). Check the execution_context for what's available.
    - **Ending a turn.** Every turn ends with
      ``return_result(RespondResult(kind=..., persist=...))``:
        * ``kind="GET_USER_INPUT"`` — wait for the next human message.
          Use after answering a question or asking a follow-up.
        * ``kind="WAIT"`` — wait for ANY declared input queue (useful
          when a background job is running alongside the conversation).
        * ``kind="STOP"`` — end the session on explicit "we're done"
          signals (``/exit``, "quit", etc.).

    # Communication mechanics

    ``self.message(text)`` — Markdown to the user, rendered when the
    current code cell finishes. Use for final answers and for status
    the user should see. Call it multiple times per turn as needed.

    Python comments in ``execute_python`` — your internal thinking. The user
    doesn't see them but the next turn does. Use sparingly: a one-liner when
    a decision is non-obvious.

    The outer dispatcher calls ``respond(notification, restored=...)``
    once per turn. ``notification`` is a ``(queue_name, item)`` pair;
    ``restored`` is whatever dict you handed back via ``persist`` last
    time. On the very first turn, ``restored`` is ``None``.
    """

    _config: Annotated[AgentConfig, hidden, nosnapshot]
    _phase: Annotated[str, hidden]
    _workflow_state: Annotated[dict, hidden]
    bash: Annotated[BashTool, nosnapshot]
    files: Annotated[FileTool, nosnapshot]
    libs: Annotated[LibraryWriting, nosnapshot]
    todo: TodoManager
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
            config: Agent behavior configuration (summarization, orchestrator, etc.)
            **kwargs: Additional arguments passed to Agent
        """
        super().__init__(llm=llm, **kwargs)

        config = config or AgentConfig()

        # Store config for later access
        self._config = config
        # Phase tracking for multi-turn workflows
        self._phase: str = "idle"
        self._workflow_state: dict = {}

        self._skills_dirs: list = []  # set by bootstrap with CLI --skills-dir + entry points

        from nemo_oo_agents.config.tool_configs import BashConfig
        from nemo_oo_agents.paths import get_project_dir

        _project_dir = get_project_dir()
        _srt = _project_dir / "srt_settings.json"
        self.bash = BashTool(
            working_dir=config.working_dir,
            config=BashConfig(srt_settings=_srt if _srt.exists() else None),
        )
        self.files = FileTool(self.bash)
        self.libs = LibraryWriting(self, path=_project_dir / "libs")
        self.todo = TodoManager()

        # Expose context and events to the LLM
        spec(self, "context", hidden=False)
        spec(self, "events", hidden=False)

        # Show todo progress to the LLM every turn
        self.context.set_dynamic("todo_status", "self.todo.status()")

        # Show context-window usage to the LLM every turn (lets the agent
        # decide when to call /compact). Value is from the PREVIOUS turn's
        # render, so the very first respond() call sees an empty string.
        self.context.set_dynamic(
            "context_usage",
            "self.context_stats.format() if self.context_stats else ''",
        )

        # Install summarizer after agent is initialized
        if config.summarization.policy != "none":
            install_summarizer(config.summarization, agent=self)

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
            current_tokens = summarizer._estimate_tokens()
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
        fill up with the doer's scratch work. Call ``.execute(todo_id, title,
        notes)`` to run a single todo item to completion.
        """
        return DoerAgent(
            llm=self._llm,
            bash=self.bash,
            files=self.files,
            todo=self.todo,
            skills_dirs=self._skills_dirs,
        )

    @strategy(CodeActStrategy(config=CodeActConfig(cell_timeout=1800.0)))
    async def do_it(self, todo_id: str, todo_title: str, todo_notes: str) -> str:
        """Execute a single todo item and return a summary of what was done.

        Todo: [{todo_id}] {todo_title}
        Notes: {todo_notes}

        Instructions:
        1. Read the todo title and notes carefully.
        2. Execute the work described using self.bash and self.files.
        3. When done, update the todo with what you learned:
           self.todo.update("{todo_id}", notes="what you did and found")
        4. Mark it complete: self.todo.done("{todo_id}")
        5. Return a concise summary of what you did and the outcome.

        Focus only on this one item. Do NOT work on other todos.
        """
        ...

    @hidden
    @strategy(PredictStrategy())
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
           the root cause. State them explicitly via reasoning().
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

    # respond() is inherited from BaseTUIAgent and runs as a forever-loop.
    # See the BaseTUIAgent.respond docstring for the pump pattern.
