# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI Agent extending NeMo OO Agents with Bash tools and summarization.

Uses the new summarization subagent pattern from nemo_oo_agents.agents.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from nemo_oo_agents import hidden, strategy
from nemo_oo_agents.agentdoc import doc, spec
from nemo_oo_agents.storage.markers import nosnapshot
from nemo_oo_agents.storage.snapshot_vars import SnapshotVars

with hidden:
    from collections.abc import Callable

    from nemo_oo_agents import Agent
    from nemo_oo_agents.agents import TokenBudgetSummarizer
    from nemo_oo_agents.config import CodeActConfig, PredictConfig
    from nemo_oo_agents.runtime.channels import Channel, QueueManager, _ChannelReader
    from nemo_oo_agents.runtime.producers_skill import ProducersSkill
    from nemo_oo_agents.skill_registry import SkillRegistry
    from nemo_oo_agents.strategies import CodeActStrategy, PredictStrategy
    from nemo_oo_agents.tools import SkillWriting, TodoManager
    from nemo_oo_agents.tools.shell_tools import ShellTools
    from nemo_oo_agents.tools.todo import Todo
    from nemo_oo_agents.tools.web_publisher import WebPublisher
    from nemo_oo_agents_cli.tools.pyp import (
        Pyp,
    )
    from nemo_oo_agents_cli.tools.repo_tools import RepoTools

# Standard library — all visible in REPL
import asyncio  # noqa: F401
import datetime  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401

from nemo_oo_agents.runtime import producers  # noqa: F401
from nemo_oo_agents.runtime.producers import after, cron, monitor, run_job, tail  # noqa: F401

# os is used by this module (NEMO_OO_RICH_URL check) but not useful to expose to
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
    from nemo_oo_agents.unifiedllm import FakeLLMClient, UnifiedLLM

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


class RespondReason(StrEnum):
    """Reason/action returned by ``handle()`` at the end of a turn."""

    DONE = "DONE"
    NEED_INPUT = "NEED_INPUT"
    WAIT = "WAIT"
    GET_USER_INPUT = "GET_USER_INPUT"


RespondKind = Literal["DONE", "NEED_INPUT", "WAIT", "GET_USER_INPUT"]


class RespondResult(BaseModel):
    """Return value for ``handle()`` — signals what the outer loop should do next.

    Fields:

    - ``kind`` — reason/action enum:
        * ``RespondReason.DONE`` — the request is complete; dispatcher waits
          for the next user message.
        * ``RespondReason.NEED_INPUT`` — the agent asked a question or needs
          human input; dispatcher waits for the next user message.
        * ``RespondReason.WAIT`` — dispatcher races every ``InputQueue``
          declared on the agent (``wait_for_any``) and re-enters with the
          first arrival.
        * ``RespondReason.GET_USER_INPUT`` — legacy spelling for waiting on
          the next user message; prefer ``DONE`` or ``NEED_INPUT``.
    - ``explanation`` — required non-empty short reason why the agent is ending this
      turn, or what external input/background event it is waiting for. The TUI
      records and renders this line, so make it concrete: name the job/queue
      being waited on, why it matters, or what user input is needed and why.


    Use ``self.v.<name> = value`` for state that should survive across
    turns (snapshot-backed).

    Build from within the LLM's ``execute_python`` code::

        return_result(
            RespondReason.DONE,
            explanation="answered the request; waiting for the next user message",
        )

    The older explicit model form is still valid::

        return_result(RespondResult(kind="DONE", explanation="answered the request"))
    """

    kind: RespondReason = Field(description="What the outer dispatcher should do next")
    explanation: str = Field(
        min_length=1,
        description=("Required: why handle() returned, or what the dispatcher is waiting for."),
    )

    @field_validator("explanation")
    @classmethod
    def _explanation_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("explanation is required")
        return value

    model_config = {"arbitrary_types_allowed": True}


# Default LLM for class definition (overridden at instantiation)
with hidden:
    try:
        from nemo_oo_agents.unifiedllm import get_llm_client

        from .config import DEFAULT_MODEL

        _DEFAULT_LLM = get_llm_client(DEFAULT_MODEL)
    except Exception:
        from nemo_oo_agents.unifiedllm import FakeLLMClient

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


class AgentVars:
    """Attribute-access proxy for an agent's persistent ``vars`` dict.

    Mirrors ``TodoVars``: write ``self.v.spec = "..."`` instead of
    ``self.vars["spec"] = "..."``. Reads and writes go straight
    through to ``self.vars`` so snapshot serialization is unaffected.

    Use for variables that need to survive across turns and across
    sessions but aren't tied to a specific todo. (For per-todo state,
    use ``self.todo.<id>.v`` — same shape, narrower scope.)

    Values are snapshot-backed: assigning something that can't be
    snapshot-serialized (a live client, socket, callable, ...) logs a
    warning and is **not stored** — it won't survive ``/exit`` + resume.
    Store serializable data (dict/str/number/Pydantic model) instead.
    """

    def __init__(self, agent: Any):
        object.__setattr__(self, "_agent", agent)

    def __getattr__(self, key: str) -> Any:
        try:
            return self._agent.vars[key]
        except KeyError:
            raise AttributeError(f"No var {key!r} on agent") from None

    def __setattr__(self, key: str, value: Any) -> None:
        self._agent.vars[key] = value

    def __delattr__(self, key: str) -> None:
        try:
            del self._agent.vars[key]
        except KeyError:
            raise AttributeError(f"No var {key!r} on agent") from None

    def __contains__(self, key: str) -> bool:
        return key in self._agent.vars

    def __repr__(self) -> str:
        return repr(self._agent.vars)


@hidden
class BaseTUIAgent(Agent, llm=_DEFAULT_LLM):
    """Base class for agents that work with the NeMo OO Agents TUI.

    Subclass this and implement ``handle()`` to build a custom TUI agent.
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

    ``handle()`` runs *per turn* — the outer dispatcher calls it with
    the next notification (a ``dict[str, list]``). Use ``self.v`` for
    state that should survive across turns.
    """

    _render_message: Annotated[Callable[[str], None] | None, hidden, nosnapshot]
    # QueueManager owns the channel registry. Hidden from the LLM by
    # default — the LLM should access individual channels (e.g.
    # ``self.user_messages``) directly, not through a string-keyed
    # registry lookup.
    queue_manager: Annotated[QueueManager, hidden, nosnapshot]
    # Producer-side Channel (full put / pop_last / snapshot / etc.)
    # — hidden, since the LLM has no business calling those.
    _user_messages_in: Annotated[Channel, hidden, nosnapshot]
    # Slash command results — posted by Session when a @slash_command returns.
    _slash_commands_in: Annotated[Channel, hidden, nosnapshot]
    # Read-only facade (just .get() / .status() / .name) is what the
    # LLM sees as ``self.user_messages``.
    user_messages: Annotated[_ChannelReader, nosnapshot]
    # Slash command results — the agent receives SlashCommandResult objects here.
    slash_commands: Annotated[_ChannelReader, nosnapshot]
    # Persistent variables for the LLM — survives across turns AND
    # across sessions (snapshot-backed). Accessed via the ``self.v``
    # proxy for dot-attribute reads/writes (``self.v.spec = "..."``).
    vars: SnapshotVars

    def __init__(self, llm=None, **kwargs):
        super().__init__(llm=llm or _DEFAULT_LLM, **kwargs)
        self._render_message = None
        self.vars = SnapshotVars()
        self.queue_manager = QueueManager(agent=self, event_manager=self.event_manager)
        self._user_messages_in = self.queue_manager.queue("user_messages")
        self.user_messages = self._user_messages_in.reader
        self._slash_commands_in = self.queue_manager.queue("slash_commands")
        self.slash_commands = self._slash_commands_in.reader
        self.producers = ProducersSkill()
        self.pyp = Pyp()
        # Surface pending-queue counts (and a short preview of each item)
        # to the LLM every turn — the agent reads queue depth straight
        # from the ``queues`` context block. Composed via
        # ``QueueManager.status()`` so adding new channels Just Works.
        self.context.set_dynamic("queues", "self.queue_manager.status()")
        if os.environ.get("NEMO_OO_RICH_URL"):
            from nemo_oo_agents.tools.web_publisher import RichOutput

            self.event_manager.register_event_type(RichOutput)
            self.web: Annotated[WebPublisher, nosnapshot] = WebPublisher(
                event_manager=self.event_manager
            )
            # The WebPublisher's doc is static across the session, so
            # it goes into the cacheable prefix with the system prompt.
            self.context_manager.set_static("web", doc(self.web))

    @property
    def v(self) -> AgentVars:
        """Attribute-access proxy for ``self.vars`` — the agent's
        persistent variable dict.

        Use for state that should survive across turns and sessions
        but isn't tied to a specific todo. Snapshot-backed via
        ``self.vars``.

        Usage::

            self.v.spec = "implement queue mode"
            self.v.cursor = 0
            print(self.v.spec)
            del self.v.cursor

        Compare:
        - REPL locals → cleared between turns.
        - ``self.v.k = v`` → snapshot-backed, survives turns + sessions.
        - ``self.todo.<t>.v.k = v`` → same as ``self.v`` but scoped to
          one todo.
        """
        return AgentVars(self)

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

    @hidden
    @strategy(PredictStrategy(PredictConfig(output_serialization="tool_call")))
    async def name_session(self, user_message: str) -> str:
        """Generate an ultra-short 2-5 word session title.

        Conversation starts with: {user_message}
        """
        ...

    @hidden
    @strategy(CodeActStrategy())
    async def handle(
        self,
        notification: dict[str, list],
    ) -> "RespondResult":
        """Handle a single turn of the conversation.

        Called once per inbound notification (or batch). Unpack
        ``notification`` and do the work, then return a
        ``RespondResult`` telling the outer dispatcher what to do next.

        Use ``self.v.<name> = value`` for state that should survive
        across turns (snapshot-backed via ``self.vars``).

        ## Turn anatomy

        Notification is always ``dict[str, list]`` — channel name →
        list of items that arrived since last turn::

            msgs = notification.get("user_messages", [])
            lines = notification.get("ci", [])

        ## Work ethic

        Do ALL the work before returning. Use as many ``execute_python``
        calls as needed — explore, implement, test, iterate. A turn that
        returns after one or two cells when the task clearly needs more
        is a bug. The only reasons to call ``return_result`` are:

        1. You have genuinely completed everything the user asked for.
        2. You need user input to proceed (ambiguity, confirmation).
        3. You are waiting on a background job.

        ## Returning

        End the turn with exactly one ``return_result(REASON_ENUM, explanation="...")``.
        ``explanation`` is required and must be non-empty. The TUI records and renders it as the
        visible stop reason, so be specific and user-facing: if waiting on a
        job/queue, name which job and why; if asking for input, say what input
        is needed and why.

        - Request complete; wait for the next user message::

              return_result(RespondReason.DONE, explanation="implemented the feature and verified focused tests")

        - Need human input before proceeding::

              return_result(RespondReason.NEED_INPUT, explanation="need the target branch before pushing the MR")

        - Wait for ANY producer queue (user or background job)::

              return_result(RespondReason.WAIT, explanation="waiting for pytest job ci-42 to finish before reporting results")



        ## Available queues

        The dispatcher delivers the next item via ``notification``.
        You can also dequeue extra items mid-turn (without ending it)
        by awaiting the queue directly::

            extra = await self.user_messages.get()  # blocks until next message

        Recognized ``queue_name`` values:

        - ``"user_messages"`` — text from the human; ``item`` is a str.
        - ``"slash_commands"`` — results from slash commands; ``item`` is a
          ``SlashCommandResult(command, args, value, text)``. Access the
          raw return value via ``item.value``; ``str(item)`` gives the text.

        Subclasses may add more (e.g. ``"job_outputs"``); the
        ``<queue_status>`` context block lists the pending count per
        queue each turn. When you return ``kind="WAIT"`` the dispatcher
        races every declared queue and re-enters with the first arrival.
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
    blocking the turn with ``self.bash.run()``:

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
    _phase: Annotated[str, hidden]
    _workflow_state: Annotated[dict, hidden]
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

        from nemo_oo_agents.paths import get_project_dir

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
        self.skills.activate(["nemo.*"])

        # Render Python tool docs together so ShellTools/RepoTools stay paired in context.
        self.context.set_static("python_tools", expr="doc(RepoTools, ShellTools)")

        # Skills register their own context blocks via context_block class attr

        # Expose context and events to the LLM
        spec(self, "context", hidden=False)
        spec(self, "events", hidden=False)

        # Show context-window usage to the LLM every turn (lets the agent
        # decide when to call /compact). Value is from the PREVIOUS turn's
        # render, so the very first respond() call sees an empty string.
        self.context.set_dynamic(
            "context_usage",
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
            current_tokens = stats.total_tokens if stats else 0
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
        return DoerAgent(
            llm=self._llm,
            cwd=self.shell.cwd,
            repo=self.repo,
            todo=self.todo,
            skills_dirs=self._skills_dirs,
            shell_cls=type(
                self.shell
            ),  # propagate the parent's current shell variant (/swap-shell)
        )

    @strategy(CodeActStrategy())
    async def do_it(self, todo: Todo) -> str:
        """Execute a single todo item and return a summary of what was done.

        Todo: [{todo.id}] {todo.title}
        Notes: {todo.notes}

        Instructions:
        1. Read the todo title and notes carefully.
        2. Execute the work described using self.shell (bash, view, edit, write, grep, find, ls).
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
