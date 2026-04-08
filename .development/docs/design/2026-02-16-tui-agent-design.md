# TUI Agent Redesign: Design Document

**Date**: 2026-02-16
**Status**: Brainstorming — awaiting design approval
**Prerequisites**: See `AGENTS.md` for framework overview. For details: `docs/guides/prompt-mechanics.md`, `docs/guides/strategies.md`, `docs/guides/single-vs-multi-agent.md`.

---

## Problem Statement

The TUI agent has excellent infrastructure (bash, files, MCP, skills, history summarization, tracing) but the agent itself is a generic "helpful assistant" with:
- No task classification
- No workflow routing
- No quality gates (tests, verification, review)
- Skills loaded as passive context, not enforced workflows

See `docs/tui-agent-superpowers-gap-analysis.md` for the full gap analysis.

## Design Constraints

- **Target users**: External developers using agent006 as a library
- **Enforcement**: Full workflow — the agent always follows the process (brainstorm → plan → TDD → verify → review)
- **Architecture**: Single agent, multi-method (see best practices doc for rationale: conversation continuity, simplicity, history summarizer handles context bloat)

---

## Architectural Approaches Considered

Three approaches were evaluated. See best practices doc for full single-agent vs multi-agent analysis.

### Approach A: Single Agent, Multi-Method (Selected)

One `TUIAgent` class. Pure Python orchestrator enforces workflow sequence. `@strategy` methods handle each LLM-powered phase.

```
TUIAgent
├── respond(msg)          # Pure Python orchestrator — classifies & routes
├── classify_intent(msg)  # @strategy → returns Intent
├── brainstorm(request)   # @strategy → explores requirements
├── write_plan(spec)      # @strategy → creates numbered plan
├── implement_step(step)  # @strategy → TDD: test → implement → verify
├── debug_issue(desc)     # @strategy → reproduce → hypothesize → verify → fix
├── verify_work()         # @strategy → runs tests, checks diff
└── review_changes(plan)  # @strategy → reviews diff against plan
```

**Selected because**: Conversation continuity (user chats with one agent), simplicity (one class, one file), history summarizer already handles context bloat, context blocks persist naturally across methods.

### Approach B: Orchestrator + Subagents (Not selected for TUI)

Separate Agent subclasses per phase. Better for benchmarks (DABStep pattern) where context isolation matters and there's no human conversation.

### Approach C: Phase-Based Pipeline (Not selected)

Generic `run_phase()` with dynamic instructions. Flexible but harder to debug and less educational.

---

## REPL Input Routing

The REPL loop (`tui/main.py`) handles three input categories before the agent sees anything:

```
User input
    │
    ├─ starts with "/"  → CommandHandler (existing: /skills, /mcp, /model, /help)
    ├─ starts with "!"  → Direct bash execution (NEW — bypasses agent entirely)
    └─ anything else    → agent.respond(user_input)
```

### `!` Prefix — Direct Bash

For quick shell commands without agent overhead. The REPL strips the `!` and runs the rest through `agent.bash.run()` directly, printing stdout/stderr:

```python
# In main.py REPL loop:
if user_input.startswith("!"):
    cmd = user_input[1:].strip()
    if cmd:
        result = await agent.bash.run(cmd)
        console.print_bash_output(result)
    continue
```

Examples:
- `!git status` — check git state
- `!pytest tests/ -v` — run tests
- `!ls -la src/` — list files

This is intentionally raw — no LLM, no history, no tracing. For when you just want a shell.

---

## Proposed Design

### Multi-Turn and Phase Tracking

**How multi-turn works** (confirmed from `tui/main.py` and `tui/agent.py`):

The REPL calls `agent.respond(user_input)` once per user message. Within CodeAct, the LLM can call `message()` to send output to the user, but `message()` does NOT block for input. The user's response arrives as the *next* `respond()` call. Multi-turn works because events accumulate on the same agent instance — the LLM sees the full conversation history.

**Implication for the orchestrator**: The orchestrator can't run `brainstorm()` → `write_plan()` → `implement()` in a single `respond()` call when brainstorming needs user input. It needs **phase tracking** to resume the workflow across multiple `respond()` calls.

```python
class TUIAgent(Agent, llm=llm):
    def __init__(self, ...):
        super().__init__(...)
        self._phase: str = "idle"           # Current workflow phase
        self._workflow_state: dict = {}     # Accumulated state across phases

    async def respond(self, user_message: str):
        """Pure Python orchestrator. Tracks phase across calls."""

        # If we're in the middle of a workflow, resume that phase
        if self._phase == "brainstorming":
            return await self._continue_brainstorm(user_message)
        if self._phase == "awaiting_plan_approval":
            return await self._handle_plan_approval(user_message)

        # New task — classify and start workflow
        intent = await self.classify_intent(user_message)

        if intent.task_type == "question":
            return await self.answer_question(user_message)

        if intent.task_type == "feature":
            self._phase = "brainstorming"
            spec = await self.brainstorm(user_message)
            if spec is None:
                # brainstorm() asked the user a question, waiting for response
                return
            await self._proceed_to_plan(spec)

        if intent.task_type == "bugfix":
            diagnosis = await self.debug_issue(user_message)
            await self._verify_and_complete()

        if intent.task_type == "refactor":
            self._phase = "planning"
            plan = await self.write_plan(user_message)
            await self._execute_plan(plan)

    async def _continue_brainstorm(self, user_message: str):
        """Resume brainstorming with user's answer."""
        spec = await self.brainstorm(user_message)
        if spec is None:
            return  # Still asking questions
        await self._proceed_to_plan(spec)

    async def _proceed_to_plan(self, spec: BrainstormResult):
        """Transition from brainstorm to planning."""
        self.context["brainstorm_decisions"] = spec.model_dump_json()
        self._phase = "planning"
        plan = await self.write_plan(spec)
        # Plan is presented to user via message() — wait for approval
        self._workflow_state["plan"] = plan
        self._phase = "awaiting_plan_approval"

    async def _handle_plan_approval(self, user_message: str):
        """Handle user's response to plan presentation."""
        plan = self._workflow_state["plan"]
        # Let the LLM interpret whether the user approved
        approved = await self.check_approval(user_message)
        if not approved:
            # Revise plan
            plan = await self.write_plan(user_message)
            self._workflow_state["plan"] = plan
            return
        await self._execute_plan(plan)

    async def _execute_plan(self, plan: Plan):
        """Execute all plan steps with TDD, then verify and review."""
        self.context["plan"] = plan.model_dump_json()
        self._phase = "implementing"
        for step in plan.steps:
            await self.implement_step(step)
        await self._verify_and_complete(plan)

    async def _verify_and_complete(self, plan: Plan | None = None):
        """Verification gate — always runs before completion."""
        self._phase = "verifying"
        evidence = await self.verify_work()
        if plan:
            await self.review_changes(plan)
        self._phase = "idle"
        self._workflow_state = {}
```

### Existing Infrastructure (Preserved)

The new `TUIAgent` inherits all existing infrastructure from the current implementation. These stay as-is:

| Component | Access | Source |
|-----------|--------|--------|
| Bash execution | `self.bash` | `BashTool(working_dir=...)` |
| File operations | `self.files` | `FileTools(self.bash)` |
| MCP tool calling | `self.mcp` | `MCPHelper(mcp_manager)` |
| Skill loading | `self._skill_manager` | `SkillManager(config.skills_dirs)` |
| History summarization | Automatic | `TokenBudgetPolicy` / `SlidingWindowPolicy` |
| Context blocks | `self.context` | MCP tools, active skills, dynamic state |
| Streaming display | Automatic | `StreamingDisplay` attached to agent |

All existing context block management (`update_mcp_context()`, `update_skills_context()`) stays unchanged. The new methods (`brainstorm`, `implement_step`, etc.) can use `self.bash`, `self.files`, `self.mcp` like the current `respond()` does.

### Methods and Their Docstrings

Each method's docstring is the full instruction set for the LLM during that phase.

#### `classify_intent(msg) -> Intent`

Lightweight classification. Uses `StructuredOutputStrategy` for speed (no code execution needed).

```python
@strategy(StructuredOutputStrategy())
async def classify_intent(self, user_message: str) -> Intent:
    """Classify the user's message into a task type.

    Message: {user_message}

    Determine:
    - task_type: "question" (asking for info), "feature" (build something new),
      "bugfix" (something is broken), "refactor" (restructure existing code)
    - summary: One-sentence description of what the user wants
    """
    ...
```

#### `brainstorm(request) -> BrainstormResult`

Multi-turn exploration. The agent asks questions, user answers via subsequent `respond()` calls. Uses `message()` to communicate and eventually returns a structured result.

```python
@strategy(CodeActStrategy(max_iterations=15, allow_text_response=True))
async def brainstorm(self, request: str) -> BrainstormResult:
    """Explore {request} before writing any code.

    Your job is to understand what to build. Ask the user clarifying questions
    ONE AT A TIME about:
    - Scope: What exactly should this do? What should it NOT do?
    - Constraints: Performance requirements? Compatibility? Dependencies?
    - Location: Where should this code live? What existing patterns to follow?
    - Interface: What should the API/interface look like?

    Use message() to ask questions and wait for user responses.
    Use self.bash and self.files to explore the codebase for context.

    When you have enough understanding, return a BrainstormResult with the
    decisions made. Do NOT write implementation code."""
    ...
```

#### `write_plan(spec) -> Plan`

Takes brainstorm output, produces numbered implementation steps.

```python
@strategy(CodeActStrategy(max_iterations=10))
async def write_plan(self, spec: BrainstormResult) -> Plan:
    """Create an implementation plan based on the brainstorming results.

    Spec: {spec}

    Create a numbered list of implementation steps. Each step should be:
    - Small enough to implement and test independently
    - Ordered by dependency (foundations first)
    - Specific about which files to create/modify

    Use self.files and self.bash to check the existing codebase.
    Present the plan to the user via message() for approval before returning."""
    ...
```

#### `implement_step(step) -> StepResult`

TDD workflow for a single plan step.

```python
@strategy(CodeActStrategy(max_iterations=50))
async def implement_step(self, step: PlanStep) -> StepResult:
    """Implement {step} using test-driven development.

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
```

#### `debug_issue(description) -> DiagnosisResult`

Systematic debugging.

```python
@strategy(CodeActStrategy(max_iterations=30))
async def debug_issue(self, description: str) -> DiagnosisResult:
    """Debug: {description}

    Follow this exact sequence:
    1. REPRODUCE: Find a way to trigger the issue. Run a test or command
       that demonstrates the bug. If you cannot reproduce, ask the user.
    2. HYPOTHESIZE: Based on the reproduction, form 2-3 hypotheses about
       the root cause. State them explicitly.
    3. INVESTIGATE: For each hypothesis, run targeted investigation
       (read code, add logging, inspect state). Eliminate hypotheses.
    4. FIX: Once root cause is confirmed, implement the fix.
    5. VERIFY: Run the reproduction again. Confirm the bug is fixed.

    Do NOT jump to a fix without reproducing first.
    Do NOT guess — investigate systematically."""
    ...
```

#### `verify_work() -> VerificationResult`

Evidence-based verification.

```python
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
```

#### `review_changes(plan) -> ReviewResult`

Check implementation against plan.

```python
@strategy(CodeActStrategy(max_iterations=10))
async def review_changes(self, plan: Plan) -> ReviewResult:
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

### Return Types (Pydantic Models)

```python
class Intent(BaseModel):
    task_type: Literal["question", "feature", "bugfix", "refactor"]
    summary: str

class BrainstormResult(BaseModel):
    summary: str           # What we're building
    decisions: list[str]   # Key decisions made
    constraints: list[str] # Identified constraints
    scope: str             # What's in/out of scope

class PlanStep(BaseModel):
    number: int
    description: str
    files: list[str]       # Files to create/modify

class Plan(BaseModel):
    steps: list[PlanStep]
    summary: str

class StepResult(BaseModel):
    step_number: int
    test_file: str
    implementation_files: list[str]
    tests_pass: bool

class DiagnosisResult(BaseModel):
    root_cause: str
    fix_applied: str
    verified: bool

class VerificationResult(BaseModel):
    tests_pass: bool
    test_output: str
    lint_clean: bool
    diff_summary: str

class ReviewResult(BaseModel):
    complete: bool
    issues: list[str]
    summary: str
```

---

## Resolved Questions

1. **Multi-turn during brainstorming**: Confirmed from `tui/main.py` — the REPL calls `respond()` once per user message. `message()` sends output but doesn't block. Multi-turn works across multiple `respond()` calls because events accumulate on the same agent instance. The orchestrator uses **phase tracking** (`self._phase`) to resume the workflow where it left off.

2. **Existing infrastructure reuse**: All existing tools (`BashTool`, `FileTools`, `MCPHelper`, `SkillManager`) stay on the agent instance as `self.bash`, `self.files`, `self.mcp`, `self._skill_manager`. Context block management (`update_mcp_context()`, `update_skills_context()`) is unchanged.

3. **Slash commands**: Already handled — the REPL checks `user_input.startswith("/")` before calling `respond()`. Slash commands never reach the orchestrator.

4. **Direct bash (`!` prefix)**: New feature — `!command` in the REPL runs the command directly through `agent.bash.run()`, bypassing the agent entirely. No LLM, no history, no tracing.

5. **Simple tasks**: `classify_intent()` routes "question" type to `answer_question()` — a simple CodeAct method with `allow_text_response=True` and no workflow overhead.
