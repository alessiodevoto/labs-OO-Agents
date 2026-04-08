# TUI Agent Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure TUIAgent from a single generic `respond()` method into a multi-method agent with enforced workflow phases (classify → brainstorm → plan → TDD implement → verify → review).

**Architecture:** Single agent class, pure Python orchestrator routes to `@strategy` methods. Phase tracking via `self._phase` resumes workflows across REPL turns. All existing infrastructure (bash, files, mcp, skills, summarizer) preserved.

**Tech Stack:** Python 3.12, nemo_oo_agents framework, Pydantic v2 for return types, pytest for tests.

**Branch:** `feat/tui` (contains `allow_text_response` and all TUI infrastructure).

---

## Task 1: Create Pydantic Return Type Models

**Files:**
- Create: `tui/models.py`
- Test: `tui/tests/test_models.py`

**Step 1: Write the model validation tests**

```python
# tui/tests/test_models.py
"""Tests for TUI agent Pydantic return types."""
import pytest
from tui.models import (
    Intent, BrainstormResult, PlanStep, Plan,
    StepResult, DiagnosisResult, VerificationResult, ReviewResult,
)


class TestIntent:
    def test_valid_feature(self):
        intent = Intent(task_type="feature", summary="Add login page")
        assert intent.task_type == "feature"

    def test_valid_question(self):
        intent = Intent(task_type="question", summary="How does auth work?")
        assert intent.task_type == "question"

    def test_invalid_task_type(self):
        with pytest.raises(Exception):  # Pydantic validation error
            Intent(task_type="invalid", summary="test")

    def test_missing_summary(self):
        with pytest.raises(Exception):
            Intent(task_type="feature")


class TestBrainstormResult:
    def test_complete(self):
        result = BrainstormResult(
            complete=True,
            summary="Build OAuth login",
            decisions=["Use Google OAuth"],
            constraints=["Must support SSO"],
            scope="Login page only",
        )
        assert result.complete is True
        assert result.pending_question is None

    def test_incomplete_with_question(self):
        result = BrainstormResult(
            complete=False,
            summary="Exploring login options",
            pending_question="Which OAuth provider?",
        )
        assert result.complete is False
        assert result.pending_question == "Which OAuth provider?"

    def test_defaults(self):
        result = BrainstormResult(complete=True, summary="Done")
        assert result.decisions == []
        assert result.constraints == []
        assert result.scope == ""


class TestPlan:
    def test_plan_with_steps(self):
        plan = Plan(
            summary="Add login feature",
            steps=[
                PlanStep(number=1, description="Create auth module", files=["src/auth.py"]),
                PlanStep(number=2, description="Add tests", files=["tests/test_auth.py"]),
            ],
        )
        assert len(plan.steps) == 2
        assert plan.steps[0].number == 1


class TestStepResult:
    def test_passing_step(self):
        result = StepResult(
            step_number=1,
            test_file="tests/test_auth.py",
            implementation_files=["src/auth.py"],
            tests_pass=True,
        )
        assert result.tests_pass is True


class TestDiagnosisResult:
    def test_verified_fix(self):
        result = DiagnosisResult(
            root_cause="Off-by-one in loop",
            fix_applied="Changed range(n) to range(n+1)",
            verified=True,
        )
        assert result.verified is True


class TestVerificationResult:
    def test_all_pass(self):
        result = VerificationResult(
            tests_pass=True,
            test_output="5 passed",
            lint_clean=True,
            diff_summary="2 files changed",
        )
        assert result.tests_pass is True


class TestReviewResult:
    def test_complete_with_no_issues(self):
        result = ReviewResult(complete=True, issues=[], summary="All good")
        assert result.complete is True
        assert result.issues == []
```

**Step 2: Run tests to verify they fail**

Run: `pytest tui/tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tui.models'`

**Step 3: Write the models**

```python
# tui/models.py
"""Pydantic return types for TUI agent workflow methods."""
from typing import Literal

from pydantic import BaseModel, Field


class Intent(BaseModel):
    """Classified user intent."""
    task_type: Literal["question", "feature", "bugfix", "refactor"]
    summary: str = Field(description="One-sentence description of what the user wants")


class BrainstormResult(BaseModel):
    """Result of a brainstorming phase iteration."""
    complete: bool = Field(description="True when ready to plan, False when still exploring")
    summary: str = Field(description="What we understand so far")
    decisions: list[str] = Field(default_factory=list, description="Key decisions made")
    constraints: list[str] = Field(default_factory=list, description="Identified constraints")
    scope: str = Field(default="", description="What's in/out of scope")
    pending_question: str | None = Field(default=None, description="Question asked to user, if any")


class PlanStep(BaseModel):
    """A single step in an implementation plan."""
    number: int
    description: str
    files: list[str] = Field(description="Files to create or modify")


class Plan(BaseModel):
    """Implementation plan with ordered steps."""
    steps: list[PlanStep]
    summary: str


class StepResult(BaseModel):
    """Result of implementing a single plan step."""
    step_number: int
    test_file: str
    implementation_files: list[str]
    tests_pass: bool


class DiagnosisResult(BaseModel):
    """Result of debugging an issue."""
    root_cause: str
    fix_applied: str
    verified: bool


class VerificationResult(BaseModel):
    """Result of running verification checks."""
    tests_pass: bool
    test_output: str
    lint_clean: bool
    diff_summary: str


class ReviewResult(BaseModel):
    """Result of reviewing changes against a plan."""
    complete: bool
    issues: list[str]
    summary: str
```

**Step 4: Run tests to verify they pass**

Run: `pytest tui/tests/test_models.py -v`
Expected: PASS (all tests green)

**Step 5: Commit**

```bash
git add tui/models.py tui/tests/test_models.py
git commit -m "feat(tui): add Pydantic return types for workflow methods"
```

---

## Task 2: Add `!` Prefix Direct Bash to REPL

**Files:**
- Modify: `tui/main.py` (REPL loop, around line 204 where `/` commands are handled)
- Test: `tui/tests/test_main.py` (or add to existing test file)

**Step 1: Write the test**

```python
# tui/tests/test_bang_prefix.py
"""Tests for ! prefix direct bash execution in the REPL."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tui.tools import BashResult


@pytest.mark.asyncio
async def test_bang_prefix_runs_bash_directly():
    """! prefix should run command through bash, not the agent."""
    mock_bash = AsyncMock()
    mock_bash.run.return_value = BashResult(stdout="hello\n", stderr="", returncode=0)

    # Import the helper we'll extract for testability
    from tui.main import handle_bang_command
    result = await handle_bang_command("!echo hello", mock_bash)

    mock_bash.run.assert_called_once_with("echo hello")
    assert result.stdout == "hello\n"


@pytest.mark.asyncio
async def test_bang_prefix_strips_whitespace():
    mock_bash = AsyncMock()
    mock_bash.run.return_value = BashResult(stdout="", stderr="", returncode=0)

    from tui.main import handle_bang_command
    await handle_bang_command("!  git status  ", mock_bash)

    mock_bash.run.assert_called_once_with("git status")


@pytest.mark.asyncio
async def test_bang_prefix_empty_command_returns_none():
    mock_bash = AsyncMock()

    from tui.main import handle_bang_command
    result = await handle_bang_command("!", mock_bash)

    assert result is None
    mock_bash.run.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tui/tests/test_bang_prefix.py -v`
Expected: FAIL with `ImportError: cannot import name 'handle_bang_command'`

**Step 3: Add `handle_bang_command` function and REPL integration**

Add to `tui/main.py` (near the top, after imports):

```python
async def handle_bang_command(user_input: str, bash) -> "BashResult | None":
    """Handle ! prefix — run command directly through bash, bypassing agent."""
    cmd = user_input[1:].strip()
    if not cmd:
        return None
    return await bash.run(cmd)
```

Then in the REPL loop (after the `/` command check, before the agent call):

```python
            # Handle ! prefix — direct bash execution
            if user_input.startswith("!"):
                result = await handle_bang_command(user_input, agent.bash)
                if result is not None:
                    if result.stdout:
                        console.console.print(result.stdout, end="")
                    if result.stderr:
                        console.console.print(result.stderr, end="", style="red")
                continue
```

**Step 4: Run tests to verify they pass**

Run: `pytest tui/tests/test_bang_prefix.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tui/main.py tui/tests/test_bang_prefix.py
git commit -m "feat(tui): add ! prefix for direct bash execution"
```

---

## Task 3: Add `classify_intent()` Method

**Files:**
- Modify: `tui/agent.py` (add method + import models)
- Test: `tui/tests/test_classify_intent.py`

**Step 1: Write the test**

```python
# tui/tests/test_classify_intent.py
"""Tests for intent classification method."""
import pytest
from unittest.mock import AsyncMock, patch
from tui.models import Intent


@pytest.mark.asyncio
async def test_classify_intent_returns_intent():
    """classify_intent should return an Intent with valid task_type."""
    # We test the method exists and has correct signature/decorator
    from tui.agent import TUIAgent

    # Check method exists and has strategy decorator
    method = getattr(TUIAgent, 'classify_intent', None)
    assert method is not None, "TUIAgent must have classify_intent method"

    # Check return annotation
    import inspect
    sig = inspect.signature(method)
    assert sig.return_annotation is Intent, f"Expected Intent return type, got {sig.return_annotation}"

    # Check parameters
    params = list(sig.parameters.keys())
    assert "user_message" in params, "classify_intent must accept user_message parameter"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tui/tests/test_classify_intent.py -v`
Expected: FAIL with `AttributeError: type object 'TUIAgent' has no attribute 'classify_intent'`

**Step 3: Add the method to TUIAgent**

Add import at top of `tui/agent.py`:

```python
from nemo_oo_agents.strategies import CodeActStrategy, StructuredOutputStrategy
from .models import (
    Intent, BrainstormResult, Plan, PlanStep,
    StepResult, DiagnosisResult, VerificationResult, ReviewResult,
)
```

Add method to `TUIAgent` class (before `respond`):

```python
    @strategy(StructuredOutputStrategy())
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
```

**Step 4: Run tests to verify they pass**

Run: `pytest tui/tests/test_classify_intent.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tui/agent.py tui/tests/test_classify_intent.py
git commit -m "feat(tui): add classify_intent method with StructuredOutputStrategy"
```

---

## Task 4: Add `answer_question()` Method

**Files:**
- Modify: `tui/agent.py`
- Test: `tui/tests/test_agent_methods.py` (reusable test file for method signatures)

**Step 1: Write the test**

```python
# tui/tests/test_agent_methods.py
"""Tests for TUIAgent method signatures and decorators."""
import inspect
import pytest
from tui.models import (
    Intent, BrainstormResult, Plan, PlanStep,
    StepResult, DiagnosisResult, VerificationResult, ReviewResult,
)


def _get_method(name):
    from tui.agent import TUIAgent
    method = getattr(TUIAgent, name, None)
    assert method is not None, f"TUIAgent must have {name} method"
    return method


class TestAnswerQuestion:
    def test_method_exists(self):
        _get_method("answer_question")

    def test_return_type_is_none(self):
        sig = inspect.signature(_get_method("answer_question"))
        assert sig.return_annotation is None

    def test_accepts_user_message(self):
        params = list(inspect.signature(_get_method("answer_question")).parameters.keys())
        assert "user_message" in params
```

**Step 2: Run tests to verify they fail**

Run: `pytest tui/tests/test_agent_methods.py::TestAnswerQuestion -v`
Expected: FAIL with `TUIAgent must have answer_question method`

**Step 3: Add the method**

Add to `TUIAgent` class in `tui/agent.py`:

```python
    @strategy(CodeActStrategy(max_iterations=30, allow_text_response=True))
    async def answer_question(self, user_message: str) -> None:
        """Answer the user's question or handle their simple request.

        User message: {user_message}

        Use self.bash, self.files, and self.mcp to gather information.
        Use message() to respond to the user with a clear, helpful answer.
        Use Markdown formatting for readability.
        """
        ...
```

**Step 4: Run tests to verify they pass**

Run: `pytest tui/tests/test_agent_methods.py::TestAnswerQuestion -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tui/agent.py tui/tests/test_agent_methods.py
git commit -m "feat(tui): add answer_question method for simple requests"
```

---

## Task 5: Add Remaining Strategy Methods (brainstorm, write_plan, implement_step, debug_issue, verify_work, review_changes)

**Files:**
- Modify: `tui/agent.py`
- Modify: `tui/tests/test_agent_methods.py` (add test classes)

**Step 1: Write tests for all remaining methods**

Append to `tui/tests/test_agent_methods.py`:

```python
class TestBrainstorm:
    def test_method_exists(self):
        _get_method("brainstorm")

    def test_return_type(self):
        sig = inspect.signature(_get_method("brainstorm"))
        assert sig.return_annotation is BrainstormResult

    def test_accepts_request(self):
        params = list(inspect.signature(_get_method("brainstorm")).parameters.keys())
        assert "request" in params


class TestWritePlan:
    def test_method_exists(self):
        _get_method("write_plan")

    def test_return_type(self):
        sig = inspect.signature(_get_method("write_plan"))
        assert sig.return_annotation is Plan

    def test_accepts_spec(self):
        params = list(inspect.signature(_get_method("write_plan")).parameters.keys())
        assert "spec" in params


class TestImplementStep:
    def test_method_exists(self):
        _get_method("implement_step")

    def test_return_type(self):
        sig = inspect.signature(_get_method("implement_step"))
        assert sig.return_annotation is StepResult

    def test_accepts_step(self):
        params = list(inspect.signature(_get_method("implement_step")).parameters.keys())
        assert "step" in params


class TestDebugIssue:
    def test_method_exists(self):
        _get_method("debug_issue")

    def test_return_type(self):
        sig = inspect.signature(_get_method("debug_issue"))
        assert sig.return_annotation is DiagnosisResult

    def test_accepts_description(self):
        params = list(inspect.signature(_get_method("debug_issue")).parameters.keys())
        assert "description" in params


class TestVerifyWork:
    def test_method_exists(self):
        _get_method("verify_work")

    def test_return_type(self):
        sig = inspect.signature(_get_method("verify_work"))
        assert sig.return_annotation is VerificationResult


class TestReviewChanges:
    def test_method_exists(self):
        _get_method("review_changes")

    def test_return_type(self):
        sig = inspect.signature(_get_method("review_changes"))
        assert sig.return_annotation is ReviewResult

    def test_accepts_plan(self):
        params = list(inspect.signature(_get_method("review_changes")).parameters.keys())
        assert "plan" in params
```

**Step 2: Run tests to verify they fail**

Run: `pytest tui/tests/test_agent_methods.py -v`
Expected: FAIL for all new test classes (methods don't exist yet)

**Step 3: Add all methods to TUIAgent**

Add to `TUIAgent` class in `tui/agent.py`:

```python
    @strategy(CodeActStrategy(max_iterations=15))
    async def brainstorm(self, request: str) -> BrainstormResult:
        """Explore requirements for: {request}

        Your job is to understand what to build BEFORE writing any code.

        Ask the user clarifying questions ONE AT A TIME about:
        - Scope: What exactly should this do? What should it NOT do?
        - Constraints: Performance requirements? Compatibility? Dependencies?
        - Location: Where should this code live? What existing patterns to follow?
        - Interface: What should the API/interface look like?

        Use message() to ask each question.
        Use self.bash and self.files to explore the codebase for context.

        When you have enough understanding, call return_result() with complete=True
        and all the decisions captured.

        If you asked the user a question and need their response, call return_result()
        with complete=False and pending_question set to the question you asked.

        Do NOT write implementation code. Do NOT skip asking questions."""
        ...

    @strategy(CodeActStrategy(max_iterations=10))
    async def write_plan(self, spec: str) -> Plan:
        """Create an implementation plan.

        Brainstorm results: {spec}

        Create a numbered list of implementation steps. Each step should be:
        - Small enough to implement and test independently
        - Ordered by dependency (foundations first)
        - Specific about which files to create/modify

        Use self.files and self.bash to check the existing codebase for patterns.
        Present the plan to the user via message() before returning it."""
        ...

    @strategy(CodeActStrategy(max_iterations=50))
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

    @strategy(CodeActStrategy(max_iterations=30))
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

    @strategy(CodeActStrategy(max_iterations=10))
    async def verify_work(self) -> VerificationResult:
        """Verify that recent changes work correctly.

        Run ALL of these checks:
        1. Run the test suite: await self.bash.run("pytest --tb=short -q")
        2. Check for lint/type errors if applicable
        3. Review the git diff: await self.bash.run("git diff")

        Report what you found. Do NOT claim success without evidence.
        If tests fail, report which ones and why."""
        ...

    @strategy(CodeActStrategy(max_iterations=10))
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
```

**Important design note on parameter types**: `write_plan(spec: str)`, `implement_step(step: str)`, and `review_changes(plan: str)` take `str` not Pydantic models. The orchestrator serializes them with `.model_dump_json()` before passing. This is because docstring `{param}` expansion works with strings — it substitutes the parameter value directly into the prompt.

**Step 4: Run tests to verify they pass**

Run: `pytest tui/tests/test_agent_methods.py -v`
Expected: PASS (all method signature tests green)

**Step 5: Commit**

```bash
git add tui/agent.py tui/tests/test_agent_methods.py
git commit -m "feat(tui): add brainstorm, plan, implement, debug, verify, review methods"
```

---

## Task 6: Build the Orchestrator (replace `respond()`)

This is the core task. Replace the current `respond()` with a pure Python orchestrator that classifies intent and routes through workflow phases.

**Files:**
- Modify: `tui/agent.py` (replace `respond()`, add `__init__` state, add private helper methods)
- Test: `tui/tests/test_orchestrator.py`

**Step 1: Write orchestrator routing tests**

```python
# tui/tests/test_orchestrator.py
"""Tests for the TUI agent orchestrator (respond method routing and phase tracking)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tui.models import (
    Intent, BrainstormResult, Plan, PlanStep,
    StepResult, VerificationResult, ReviewResult, DiagnosisResult,
)


@pytest.fixture
def mock_agent():
    """Create a TUIAgent-like mock with all workflow methods."""
    agent = MagicMock()
    agent._phase = "idle"
    agent._workflow_state = {}
    agent.context = {}

    # Mock all strategy methods as async
    agent.classify_intent = AsyncMock()
    agent.answer_question = AsyncMock()
    agent.brainstorm = AsyncMock()
    agent.write_plan = AsyncMock()
    agent.implement_step = AsyncMock()
    agent.debug_issue = AsyncMock()
    agent.verify_work = AsyncMock()
    agent.review_changes = AsyncMock()
    return agent


class TestIntentRouting:
    """Test that classify_intent routes to the correct workflow."""

    @pytest.mark.asyncio
    async def test_question_routes_to_answer(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(
            task_type="question", summary="How does X work?"
        )
        # Import and call the orchestrator logic directly
        from tui.agent import _orchestrate
        await _orchestrate(mock_agent, "How does X work?")

        mock_agent.answer_question.assert_called_once_with("How does X work?")
        mock_agent.brainstorm.assert_not_called()

    @pytest.mark.asyncio
    async def test_feature_starts_brainstorming(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(
            task_type="feature", summary="Add login"
        )
        mock_agent.brainstorm.return_value = BrainstormResult(
            complete=False, summary="Exploring", pending_question="Which auth?"
        )
        from tui.agent import _orchestrate
        await _orchestrate(mock_agent, "Add a login page")

        mock_agent.brainstorm.assert_called_once()
        assert mock_agent._phase == "brainstorming"

    @pytest.mark.asyncio
    async def test_bugfix_routes_to_debug(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(
            task_type="bugfix", summary="Login is broken"
        )
        mock_agent.debug_issue.return_value = DiagnosisResult(
            root_cause="Null pointer", fix_applied="Added null check", verified=True
        )
        mock_agent.verify_work.return_value = VerificationResult(
            tests_pass=True, test_output="OK", lint_clean=True, diff_summary="1 file"
        )
        from tui.agent import _orchestrate
        await _orchestrate(mock_agent, "Login is broken")

        mock_agent.debug_issue.assert_called_once()
        mock_agent.verify_work.assert_called_once()


class TestPhaseTracking:
    """Test that phase tracking resumes workflows correctly."""

    @pytest.mark.asyncio
    async def test_brainstorming_resumes(self, mock_agent):
        """When phase is brainstorming, route directly to brainstorm."""
        mock_agent._phase = "brainstorming"
        mock_agent.brainstorm.return_value = BrainstormResult(
            complete=True,
            summary="Build OAuth login",
            decisions=["Use Google"],
            scope="Login only",
        )
        mock_agent.write_plan.return_value = Plan(
            summary="OAuth plan",
            steps=[PlanStep(number=1, description="Setup", files=["auth.py"])],
        )
        from tui.agent import _orchestrate
        await _orchestrate(mock_agent, "Use Google OAuth")

        # Should NOT call classify_intent (we're mid-workflow)
        mock_agent.classify_intent.assert_not_called()
        mock_agent.brainstorm.assert_called_once()

    @pytest.mark.asyncio
    async def test_awaiting_plan_approval_approved(self, mock_agent):
        """When phase is awaiting_plan_approval, 'yes' should execute plan."""
        mock_agent._phase = "awaiting_plan_approval"
        plan = Plan(
            summary="The plan",
            steps=[PlanStep(number=1, description="Do it", files=["f.py"])],
        )
        mock_agent._workflow_state = {"plan": plan}
        mock_agent.implement_step.return_value = StepResult(
            step_number=1, test_file="t.py", implementation_files=["f.py"], tests_pass=True
        )
        mock_agent.verify_work.return_value = VerificationResult(
            tests_pass=True, test_output="OK", lint_clean=True, diff_summary="1 file"
        )
        mock_agent.review_changes.return_value = ReviewResult(
            complete=True, issues=[], summary="OK"
        )
        from tui.agent import _orchestrate
        await _orchestrate(mock_agent, "looks good, go ahead")

        mock_agent.implement_step.assert_called_once()
        mock_agent.verify_work.assert_called_once()
        assert mock_agent._phase == "idle"

    @pytest.mark.asyncio
    async def test_idle_after_completion(self, mock_agent):
        """After full workflow, phase returns to idle."""
        mock_agent.classify_intent.return_value = Intent(
            task_type="refactor", summary="Clean up auth"
        )
        mock_agent.write_plan.return_value = Plan(
            summary="Refactor plan",
            steps=[PlanStep(number=1, description="Extract", files=["auth.py"])],
        )
        mock_agent.implement_step.return_value = StepResult(
            step_number=1, test_file="t.py", implementation_files=["auth.py"], tests_pass=True
        )
        mock_agent.verify_work.return_value = VerificationResult(
            tests_pass=True, test_output="OK", lint_clean=True, diff_summary="1 file"
        )
        mock_agent.review_changes.return_value = ReviewResult(
            complete=True, issues=[], summary="OK"
        )
        from tui.agent import _orchestrate
        await _orchestrate(mock_agent, "Refactor the auth module")

        assert mock_agent._phase == "idle"
        assert mock_agent._workflow_state == {}


class TestFeatureWorkflow:
    """Test the complete feature workflow: brainstorm → plan → implement → verify → review."""

    @pytest.mark.asyncio
    async def test_brainstorm_complete_proceeds_to_plan(self, mock_agent):
        mock_agent.classify_intent.return_value = Intent(
            task_type="feature", summary="Add feature"
        )
        spec = BrainstormResult(
            complete=True,
            summary="Build X",
            decisions=["Use Y"],
            scope="Module Z",
        )
        mock_agent.brainstorm.return_value = spec
        plan = Plan(
            summary="Plan",
            steps=[PlanStep(number=1, description="Step 1", files=["f.py"])],
        )
        mock_agent.write_plan.return_value = plan

        from tui.agent import _orchestrate
        await _orchestrate(mock_agent, "Build feature X")

        mock_agent.brainstorm.assert_called_once()
        mock_agent.write_plan.assert_called_once()
        # Plan presented — waiting for approval
        assert mock_agent._phase == "awaiting_plan_approval"
        assert mock_agent._workflow_state["plan"] == plan
```

**Step 2: Run tests to verify they fail**

Run: `pytest tui/tests/test_orchestrator.py -v`
Expected: FAIL with `ImportError: cannot import name '_orchestrate'`

**Step 3: Implement the orchestrator**

Replace the existing `respond()` in `tui/agent.py` with the orchestrator. Extract the routing logic into a module-level `_orchestrate()` function for testability.

Add to `tui/agent.py` (module level, before the class):

```python
async def _orchestrate(agent: "TUIAgent", user_message: str) -> None:
    """Core orchestration logic. Extracted for testability.

    Routes user messages through workflow phases based on agent._phase state.
    """
    # If mid-workflow, resume that phase
    if agent._phase == "brainstorming":
        await _continue_brainstorm(agent, user_message)
        return

    if agent._phase == "awaiting_plan_approval":
        await _handle_plan_approval(agent, user_message)
        return

    # New task — classify and start workflow
    intent = await agent.classify_intent(user_message)

    if intent.task_type == "question":
        await agent.answer_question(user_message)
        return

    if intent.task_type == "feature":
        agent._phase = "brainstorming"
        spec = await agent.brainstorm(user_message)
        if not spec.complete:
            return  # Asked user a question, waiting for response
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


async def _continue_brainstorm(agent: "TUIAgent", user_message: str) -> None:
    """Resume brainstorming with user's answer."""
    spec = await agent.brainstorm(user_message)
    if not spec.complete:
        return  # Still asking questions
    await _proceed_to_plan(agent, spec)


async def _proceed_to_plan(agent: "TUIAgent", spec: "BrainstormResult") -> None:
    """Transition from brainstorm to planning."""
    agent.context["brainstorm_decisions"] = spec.model_dump_json()
    agent._phase = "planning"
    plan = await agent.write_plan(spec.model_dump_json())
    agent._workflow_state["plan"] = plan
    agent._phase = "awaiting_plan_approval"


async def _handle_plan_approval(agent: "TUIAgent", user_message: str) -> None:
    """Handle user's response to plan presentation.

    Simple heuristic: any message that doesn't contain rejection keywords
    is treated as approval. The LLM already presented the plan and asked
    for confirmation — the user's response is their verdict.
    """
    rejection_keywords = {"no", "change", "revise", "redo", "different", "wrong", "update"}
    words = set(user_message.lower().split())
    if words & rejection_keywords:
        # Revise plan with user feedback
        plan = await agent.write_plan(user_message)
        agent._workflow_state["plan"] = plan
        return

    plan = agent._workflow_state["plan"]
    await _execute_plan(agent, plan)


async def _execute_plan(agent: "TUIAgent", plan: "Plan") -> None:
    """Execute all plan steps with TDD, then verify and review."""
    agent.context["plan"] = plan.model_dump_json()
    agent._phase = "implementing"
    for step in plan.steps:
        await agent.implement_step(step.model_dump_json())
    await _verify_and_complete(agent, plan)


async def _verify_and_complete(agent: "TUIAgent", plan: "Plan | None" = None) -> None:
    """Verification gate — always runs before completion."""
    agent._phase = "verifying"
    await agent.verify_work()
    if plan:
        await agent.review_changes(plan.model_dump_json())
    agent._phase = "idle"
    agent._workflow_state = {}
```

Then replace the `respond()` method on `TUIAgent`:

```python
    async def respond(self, user_message: str) -> None:
        """Pure Python orchestrator. Routes to workflow methods based on intent and phase."""
        await _orchestrate(self, user_message)
```

Remove the `@strategy` decorator and the `...` body from `respond()`. It's now pure Python.

Also add phase tracking state to `__init__`:

```python
    def __init__(self, ...):
        super().__init__(llm=llm, **kwargs)

        # Phase tracking for multi-turn workflows
        self._phase: str = "idle"
        self._workflow_state: dict = {}

        # ... rest of existing __init__ ...
```

**Step 4: Run tests to verify they pass**

Run: `pytest tui/tests/test_orchestrator.py -v`
Expected: PASS

**Step 5: Run ALL tests to verify nothing broke**

Run: `pytest tui/tests/ -v`
Expected: PASS (all existing + new tests green)

**Step 6: Commit**

```bash
git add tui/agent.py tui/tests/test_orchestrator.py
git commit -m "feat(tui): replace respond() with phase-tracking orchestrator

Pure Python orchestrator classifies intent and routes through workflow
phases: brainstorm → plan → TDD implement → verify → review.
Phase tracking resumes workflows across REPL turns."
```

---

## Task 7: Update `_system_prompt()` for Multi-Method Agent

The current `_system_prompt()` is generic. Update it to reflect the new multi-method design — the LLM needs to know which phase it's in.

**Files:**
- Modify: `tui/agent.py`
- No separate test — covered by existing method tests + manual testing

**Step 1: Update `_system_prompt()`**

Replace the existing `_system_prompt()` in `tui/agent.py`:

```python
    def _system_prompt(self) -> str:
        """System prompt for TUI agent. Phase-specific instructions are in method docstrings."""
        return """You are Agent006, a development assistant running in a terminal.

You have access to these tools via self:
- self.bash — Execute shell commands (returns BashResult with .stdout, .stderr, .returncode)
- self.files — Read/write files (.read(), .write(), .str_replace(), .list(), .find(), .grep())
- self.mcp — Call MCP server tools (.call(), .list_servers(), .list_tools())

Communication:
- Use message("text") to send formatted Markdown to the user
- Use reasoning("text") to log internal thinking (not shown to user)
- Use return_result(...) to return structured results when done

Workflow:
- Execute ONE thing at a time, then observe results
- Use print() to see intermediate values
- Variables persist across executions within a method call
"""
```

**Step 2: Commit**

```bash
git add tui/agent.py
git commit -m "refactor(tui): update system prompt for multi-method agent"
```

---

## Task 8: Integration Smoke Test

Manual verification that the full loop works end-to-end.

**Files:**
- Create: `tui/tests/test_integration.py`

**Step 1: Write integration test**

```python
# tui/tests/test_integration.py
"""Integration test: verify TUIAgent can be instantiated with all new methods."""
import pytest


def test_tui_agent_instantiation():
    """TUIAgent should instantiate with all workflow methods available."""
    from tui.agent import TUIAgent

    # Check all expected methods exist
    expected_methods = [
        "respond", "classify_intent", "answer_question",
        "brainstorm", "write_plan", "implement_step",
        "debug_issue", "verify_work", "review_changes",
    ]
    for name in expected_methods:
        assert hasattr(TUIAgent, name), f"Missing method: {name}"


def test_tui_agent_phase_tracking_initial_state():
    """New TUIAgent instance should start in idle phase."""
    from tui.agent import TUIAgent
    from unittest.mock import MagicMock

    # Use a mock LLM to avoid real API calls
    mock_llm = MagicMock()
    agent = TUIAgent(llm=mock_llm)

    assert agent._phase == "idle"
    assert agent._workflow_state == {}


def test_orchestrate_function_importable():
    """_orchestrate should be importable for testing."""
    from tui.agent import _orchestrate
    assert callable(_orchestrate)
```

**Step 2: Run all tests**

Run: `pytest tui/tests/ -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tui/tests/test_integration.py
git commit -m "test(tui): add integration smoke tests for multi-method agent"
```

---

## Summary of Changes

| File | Action | What |
|------|--------|------|
| `tui/models.py` | Create | Pydantic return types (Intent, BrainstormResult, Plan, etc.) |
| `tui/agent.py` | Modify | Add 7 strategy methods, replace respond() with orchestrator, add phase tracking |
| `tui/main.py` | Modify | Add `!` prefix direct bash handling |
| `tui/tests/test_models.py` | Create | Model validation tests |
| `tui/tests/test_bang_prefix.py` | Create | `!` prefix tests |
| `tui/tests/test_classify_intent.py` | Create | classify_intent signature test |
| `tui/tests/test_agent_methods.py` | Create | Method signature tests for all strategy methods |
| `tui/tests/test_orchestrator.py` | Create | Orchestrator routing and phase tracking tests |
| `tui/tests/test_integration.py` | Create | Integration smoke tests |
