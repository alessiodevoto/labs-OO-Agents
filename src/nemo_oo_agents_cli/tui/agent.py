# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI Agent extending NeMo OO Agents with Bash tools and summarization.

Uses the new summarization subagent pattern from nemo_oo_agents.agents.
"""

from typing import Annotated

from agentdoc import doc, spec
from nemo_oo_agents import hidden, strategy
from nemo_oo_agents.storage.markers import nosnapshot

with hidden:
    from collections.abc import Callable
    from enum import Enum

    from nemo_oo_agents import Agent
    from nemo_oo_agents.agents import TokenBudgetSummarizer
    from nemo_oo_agents.config import CodeActConfig
    from nemo_oo_agents.strategies import CodeActStrategy, PredictStrategy
    from nemo_oo_agents.tools import BashTool, FileTool, LibraryWriting, TodoManager
    from nemo_oo_agents.tools.web_publisher import WebPublisher

# Standard library — all visible in REPL
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


class RespondResult(Enum):
    """Return value for respond() that controls what happens next.

    Pass one of these to ``return_result()`` at the end of your turn:

    - ``RespondResult.STOP_WORK`` — done; the TUI will NOT call respond() again.
    - ``RespondResult.WAIT_FOR_USER_INPUT`` — done for now; hand control back to the user.
    - ``RespondResult.CONTINUE_WORKING`` — the TUI will call respond() again
      immediately without waiting for the user.
    """

    STOP_WORK = "stop_work"
    WAIT_FOR_USER_INPUT = "wait_for_user_input"
    CONTINUE_WORKING = "continue_working"


# Default LLM for class definition (overridden at instantiation)
with hidden:
    try:
        from unifiedllm import get_llm_client

        from .config import DEFAULT_MODEL

        _DEFAULT_LLM = get_llm_client(DEFAULT_MODEL)
    except Exception:
        from unifiedllm import FakeLLMClient

        _DEFAULT_LLM = FakeLLMClient()


def install_summarizer(config: SummarizationConfig, agent: Agent) -> None:
    """Install a summarizer on the agent based on configuration.

    Args:
        config: Summarization configuration
        agent: Agent to install summarizer on (inherits LLM, attaches to history)
    """
    if config.policy == "none":
        return

    from nemo_oo_agents.config.summarizer_config import TokenBudgetConfig

    TokenBudgetSummarizer.install(
        agent,
        config=TokenBudgetConfig(
            max_tokens=config.max_tokens,
            preserve_recent=config.preserve_recent,
            target_chars=config.target_chars,
        ),
    )


# ---------------------------------------------------------------------------
# Orchestration functions (module-level for testability)
# ---------------------------------------------------------------------------


async def _orchestrate(agent: "TUIAgent", user_message: str) -> "RespondResult":
    """Core orchestration logic. Extracted for testability.

    Routes user messages through workflow phases based on agent._phase state.
    """
    if agent._phase == "brainstorming":
        await _continue_brainstorm(agent, user_message)
        return RespondResult.WAIT_FOR_USER_INPUT

    if agent._phase == "awaiting_plan_approval":
        await _handle_plan_approval(agent, user_message)
        return RespondResult.WAIT_FOR_USER_INPUT

    intent = await agent.classify_intent(user_message)

    if intent.task_type == "question":
        await agent.answer_question(user_message)
        return RespondResult.WAIT_FOR_USER_INPUT

    if intent.task_type == "feature":
        agent._phase = "brainstorming"
        spec = await agent.brainstorm(user_message)
        if not spec.complete:
            return RespondResult.WAIT_FOR_USER_INPUT
        await _proceed_to_plan(agent, spec)
        return RespondResult.WAIT_FOR_USER_INPUT

    if intent.task_type == "bugfix":
        await agent.debug_issue(user_message)
        await _verify_and_complete(agent)
        return RespondResult.WAIT_FOR_USER_INPUT

    if intent.task_type == "refactor":
        agent._phase = "planning"
        plan = await agent.write_plan(user_message)
        await _execute_plan(agent, plan)
        return RespondResult.WAIT_FOR_USER_INPUT

    return RespondResult.WAIT_FOR_USER_INPUT


async def _continue_brainstorm(agent: "TUIAgent", user_message: str) -> None:
    """Resume brainstorming with user's answer."""
    spec = await agent.brainstorm(user_message)
    if not spec.complete:
        return
    await _proceed_to_plan(agent, spec)


async def _proceed_to_plan(agent: "TUIAgent", spec: BrainstormResult) -> None:
    """Transition from brainstorm to planning."""
    agent.context["brainstorm_decisions"] = f"'''{spec.model_dump_json()}'''"
    agent._phase = "planning"
    plan = await agent.write_plan(spec.model_dump_json())
    agent._workflow_state["plan"] = plan
    agent._phase = "awaiting_plan_approval"


async def _handle_plan_approval(agent: "TUIAgent", user_message: str) -> None:
    """Handle user's response to plan presentation."""
    approval_keywords = {"yes", "ok", "okay", "approve", "approved", "lgtm", "proceed", "go"}
    words = set(user_message.lower().split())
    if words & approval_keywords:
        plan = agent._workflow_state["plan"]
        await _execute_plan(agent, plan)
        return

    # Treat as revision request — revise plan and stay in approval phase
    plan = await agent.write_plan(user_message)
    agent._workflow_state["plan"] = plan


async def _execute_plan(agent: "TUIAgent", plan: Plan) -> None:
    """Execute all plan steps with TDD, then verify and review."""
    agent.context["plan"] = f"'''{plan.model_dump_json()}'''"
    agent._phase = "implementing"
    for step in plan.steps:
        await agent.implement_step(step.model_dump_json())
    await _verify_and_complete(agent, plan)


async def _verify_and_complete(agent: "TUIAgent", plan: Plan | None = None) -> None:
    """Verification gate — always runs before completion."""
    agent._phase = "verifying"
    await agent.verify_work()
    if plan:
        await agent.review_changes(plan.model_dump_json())
    agent._phase = "idle"
    agent._workflow_state = {}


@hidden
class BaseTUIAgent(Agent, llm=_DEFAULT_LLM):
    """Base class for agents that work with the NeMo OO Agents TUI.

    Subclass this and implement ``respond()`` to build a custom TUI agent.
    ``message()`` and ``name_session()`` are provided for free.
    """

    _render_message: Annotated[Callable[[str], None] | None, hidden, nosnapshot]

    def __init__(self, llm=None, **kwargs):
        super().__init__(llm=llm or _DEFAULT_LLM, **kwargs)
        self._render_message = None
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
    @strategy(CodeActStrategy(config=CodeActConfig(cell_timeout=1800.0)))
    async def respond(self, user_message: str) -> "RespondResult":
        """Respond to the user's message.

        Message: {user_message}

        Use self.message() to send formatted Markdown to the user.
        Call return_result(RespondResult.WAIT_FOR_USER_INPUT) to hand control back to the user.
        Call return_result(RespondResult.STOP_WORK) when completely done.
        """
        ...


class TUIAgent(BaseTUIAgent, llm=_DEFAULT_LLM):  # type: ignore[call-arg]
    """You are NeMo OO Agents, a development assistant running in a terminal.

    Call return_result(RespondResult.WAIT_FOR_USER_INPUT) to yield back to the user.
    Call return_result(RespondResult.STOP_WORK) when completely done.

    You have access to these tools via self:
    - self.bash — Execute shell commands (returns BashResult with .stdout, .stderr, .return_code)
    - self.files — Read/write files (.read(), .write(), .edit_file(), .list(), .find(), .grep())
    - self.todo — Plan and track multi-step work (see doc(self.todo) for full API)
    - await self.do_it(id, title, notes) — Execute a todo item yourself (inline, no subagent)

    Working with todos:
    For non-trivial work, plan with todos, then execute them.

    Execute a todo yourself (inline):
        t = self.todo.add("Explore the codebase")
        result = await self.do_it(t.id, t.title, t.notes)

    Delegate to a Doer subagent (isolates context, good for complex tasks):
        dk = dict(llm=self._llm, bash=self.bash, files=self.files, todo=self.todo, skills_dirs=self._skills_dirs)
        result = await DoerAgent(**dk).execute(t.id, t.title, t.notes)

    Parallel execution (when todos are independent — use asyncio.gather):
        t1 = self.todo.add("Fix module A")
        t2 = self.todo.add("Fix module B")
        t3 = self.todo.add("Run tests", deps=[t1.id, t2.id])
        dk = dict(llm=self._llm, bash=self.bash, files=self.files, todo=self.todo, skills_dirs=self._skills_dirs)
        r1, r2 = await asyncio.gather(
            DoerAgent(**dk).execute(t1.id, t1.title, t1.notes),
            DoerAgent(**dk).execute(t2.id, t2.title, t2.notes),
        )

    The <todo_status> context block shows current progress every turn.

    Communication:
    - Use self.message("text") to send formatted Markdown to the user during your turn
    - Use Python comments to log internal thinking
    - Use return_result(RespondResult.WAIT_FOR_USER_INPUT) to end your turn — the user will then reply and you'll be called again

    This is a multi-turn conversation. return_result(RespondResult.WAIT_FOR_USER_INPUT) is like pressing
    `send`: it ends your current response and hands control back to the user. If you need more
    information, ask via self.message() then call return_result(RespondResult.WAIT_FOR_USER_INPUT) to
    wait for their reply.

    Workflow:
    - Execute ONE thing at a time, then observe results
    - Use print() to see intermediate values
    - Variables persist across executions within a method call

    Do NOT use import statements — all modules are pre-loaded. Check the
    execution_context for what's available (np, pd, px, go, json, math, etc.).

    """

    _config: Annotated[AgentConfig, hidden, nosnapshot]
    _phase: Annotated[str, hidden]
    _workflow_state: Annotated[dict, hidden]
    bash: Annotated[BashTool, nosnapshot]
    files: Annotated[FileTool, nosnapshot]
    libs: Annotated[LibraryWriting, nosnapshot]
    todo: Annotated[TodoManager, nosnapshot]
    _todo_state: Annotated[dict, hidden]  # JSON-safe snapshot of todo list
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
        # Restore todo state from snapshot if available, otherwise start fresh
        self._todo_state: dict = getattr(self, "_todo_state", {})
        self.todo = TodoManager(state=self._todo_state or None)

        # Expose context and events to the LLM
        spec(self, "context", hidden=False)
        spec(self, "events", hidden=False)

        # Show todo progress to the LLM every turn
        self.context.set_dynamic("todo_status", "self.todo.status()")

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
    async def brainstorm(self, request: str) -> BrainstormResult:
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

    @hidden
    async def respond(self, user_message: str) -> "RespondResult":
        """Respond to the user's message.

        Call return_result(RespondResult.WAIT_FOR_USER_INPUT) to yield back to the user.
        Call return_result(RespondResult.STOP_WORK) when completely done.

        When orchestrator mode is enabled (config.orchestrator=True), routes
        through workflow phases. Otherwise uses a single CodeAct strategy.
        """
        if self._config.orchestrator:
            return await _orchestrate(self, user_message)
        else:
            return await self._respond_codeact(user_message)

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=100)))
    async def _respond_codeact(self, user_message: str) -> "RespondResult":
        """Respond to the user's message using a single CodeAct strategy.

        Message: {user_message}

        Use tools to help the user.
        Use self.message() to respond with formatted Markdown.
        Call return_result(RespondResult.WAIT_FOR_USER_INPUT) to end your turn.
        Call return_result(RespondResult.STOP_WORK) when completely done.
        Execute ONE thing at a time, then observe results.
        """
        ...
