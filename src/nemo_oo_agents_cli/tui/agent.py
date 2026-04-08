"""TUI Agent extending NeMo OO Agents with Bash tools and summarization.

Uses the new summarization subagent pattern from nemo_oo_agents.agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from nemo_oo_agents import Agent, hidden, strategy
from nemo_oo_agents.agents import TokenBudgetSummarizer
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.strategies import CodeActStrategy, PredictStrategy
from nemo_oo_agents.tools import BashTool, FileTool, LibraryWriting
from unifiedllm import FakeLLMClient

from .config import AgentConfig, SummarizationConfig
from .models import (
    BrainstormResult,
    DiagnosisResult,
    Intent,
    Plan,
    ReviewResult,
    StepResult,
    VerificationResult,
)

if TYPE_CHECKING:
    from unifiedllm import UnifiedLLM


# Default LLM for class definition (overridden at instantiation)
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
        ),
    )


# ---------------------------------------------------------------------------
# Orchestration functions (module-level for testability)
# ---------------------------------------------------------------------------


async def _orchestrate(agent: TUIAgent, user_message: str) -> None:
    """Core orchestration logic. Extracted for testability.

    Routes user messages through workflow phases based on agent._phase state.
    """
    if agent._phase == "brainstorming":
        await _continue_brainstorm(agent, user_message)
        return

    if agent._phase == "awaiting_plan_approval":
        await _handle_plan_approval(agent, user_message)
        return

    intent = await agent.classify_intent(user_message)

    if intent.task_type == "question":
        await agent.answer_question(user_message)
        return

    if intent.task_type == "feature":
        agent._phase = "brainstorming"
        spec = await agent.brainstorm(user_message)
        if not spec.complete:
            return
        await _proceed_to_plan(agent, spec)
        return

    if intent.task_type == "bugfix":
        await agent.debug_issue(user_message)
        await _verify_and_complete(agent)
        return

    if intent.task_type == "refactor":
        agent._phase = "planning"
        plan = await agent.write_plan(user_message)
        await _execute_plan(agent, plan)
        return


async def _continue_brainstorm(agent: TUIAgent, user_message: str) -> None:
    """Resume brainstorming with user's answer."""
    spec = await agent.brainstorm(user_message)
    if not spec.complete:
        return
    await _proceed_to_plan(agent, spec)


async def _proceed_to_plan(agent: TUIAgent, spec: BrainstormResult) -> None:
    """Transition from brainstorm to planning."""
    agent.context["brainstorm_decisions"] = f"'''{spec.model_dump_json()}'''"
    agent._phase = "planning"
    plan = await agent.write_plan(spec.model_dump_json())
    agent._workflow_state["plan"] = plan
    agent._phase = "awaiting_plan_approval"


async def _handle_plan_approval(agent: TUIAgent, user_message: str) -> None:
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


async def _execute_plan(agent: TUIAgent, plan: Plan) -> None:
    """Execute all plan steps with TDD, then verify and review."""
    agent.context["plan"] = f"'''{plan.model_dump_json()}'''"
    agent._phase = "implementing"
    for step in plan.steps:
        await agent.implement_step(step.model_dump_json())
    await _verify_and_complete(agent, plan)


async def _verify_and_complete(agent: TUIAgent, plan: Plan | None = None) -> None:
    """Verification gate — always runs before completion."""
    agent._phase = "verifying"
    await agent.verify_work()
    if plan:
        await agent.review_changes(plan.model_dump_json())
    agent._phase = "idle"
    agent._workflow_state = {}


class TUIAgent(Agent, llm=_DEFAULT_LLM):
    """NeMo OO Agents TUI agent."""

    _config: Annotated[AgentConfig, hidden]
    _phase: Annotated[str, hidden]
    _workflow_state: Annotated[dict, hidden]

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
        super().__init__(llm=llm or _DEFAULT_LLM, **kwargs)

        config = config or AgentConfig()

        # Store config for later access
        self._config = config
        # Phase tracking for multi-turn workflows
        self._phase: str = "idle"
        self._workflow_state: dict = {}

        self.bash = BashTool(working_dir=config.working_dir)
        self.files = FileTool(self.bash)
        self.libs = LibraryWriting(self)

        # Install summarizer after agent is initialized
        if config.summarization.policy != "none":
            install_summarizer(config.summarization, agent=self)

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

    @hidden
    def _system_prompt(self) -> str:
        """System prompt for TUI agent. Phase-specific instructions are in method docstrings."""
        return """You are NeMo OO Agents, a development assistant running in a terminal.

You have access to these tools via self:
- self.bash — Execute shell commands (returns BashResult with .stdout, .stderr, .return_code)
- self.files — Read/write files (.read(), .write(), .str_replace(), .list(), .find(), .grep())

Communication:
- Use message("text") to send formatted Markdown to the user
- Use reasoning("text") to log internal thinking (not shown to user)
- Use return_result(...) to return structured results when done

Workflow:
- Execute ONE thing at a time, then observe results
- Use print() to see intermediate values
- Variables persist across executions within a method call
"""

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

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=30)))
    async def answer_question(self, user_message: str) -> None:
        """Answer the user's question or handle their simple request.

        User message: {user_message}

        Use tools to gather information.
        Use message() to respond to the user with a clear, helpful answer.
        Use Markdown formatting for readability.
        """
        ...

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=15)))
    async def brainstorm(self, request: str) -> BrainstormResult:
        """Explore requirements for: {request}

        Your job is to understand what to build BEFORE writing any code.

        Ask the user clarifying questions ONE AT A TIME about:
        - Scope: What exactly should this do? What should it NOT do?
        - Constraints: Performance requirements? Compatibility? Dependencies?
        - Location: Where should this code live? What existing patterns to follow?
        - Interface: What should the API/interface look like?

        Use message() to ask each question.
        Use tools to explore the codebase for context.

        When you have enough understanding, call return_result() with complete=True
        and all the decisions captured.

        If you asked the user a question and need their response, call return_result()
        with complete=False and pending_question set to the question you asked.

        Do NOT write implementation code. Do NOT skip asking questions."""
        ...

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))
    async def write_plan(self, spec: str) -> Plan:
        """Create an implementation plan.

        Brainstorm results: {spec}

        Create a numbered list of implementation steps. Each step should be:
        - Small enough to implement and test independently
        - Ordered by dependency (foundations first)
        - Specific about which files to create/modify

        Use tools to check the existing codebase for patterns.
        Present the plan to the user via message() before returning it."""
        ...

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=50)))
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

    async def respond(self, user_message: str) -> None:
        """Respond to the user's message.

        When orchestrator mode is enabled (config.orchestrator=True), routes
        through workflow phases. Otherwise uses a single CodeAct strategy.
        """
        if self._config.orchestrator:
            await _orchestrate(self, user_message)
        else:
            await self._respond_codeact(user_message)

    @hidden
    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=50)))
    async def _respond_codeact(self, user_message: str) -> None:
        """Respond to the user's message using a single CodeAct strategy.

        Message: {user_message}

        Use tools to help the user.
        Use message() to respond with formatted Markdown.
        Execute ONE thing at a time, then observe results.
        """
        ...
