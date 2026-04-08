# Trace Analyzer 4-Phase Design

## Overview

Restructure the trace analyzer agent from a single free-form CodeActStrategy to a structured 4-phase approach with verification loop.

## Current State

Single `diagnose()` method with CodeActStrategy(max_iterations=50):
- Free-form exploration
- Relies on agent to self-structure the analysis
- Can miss key steps or over-focus on irrelevant details

## Proposed 4-Phase Structure

### Phase 1: Overview (Reconnaissance)

**Goal:** Get the lay of the land - what happened at a high level?

**Actions:**
- Call `get_overview()` to see eval result, session structure, error summary
- Call `get_eval_context()` to see expected vs actual answer

**Output:** Structured summary:
```python
@dataclass
class OverviewResult:
    outcome: str  # SUCCESS or FAILURE
    expected: str | None
    actual: str | None
    session_count: int
    total_turns: int
    has_errors: bool
    mechanical_findings: list[str]
```

### Phase 2: Generation Call Analysis

**Goal:** Which LLM generation call(s) went wrong?

**Actions:**
- For each session, examine LLM turns
- Compare input prompt to output response
- Identify turns where the response was incorrect/problematic

**Output:**
```python
@dataclass
class GenerationAnalysis:
    session_id: str
    turn_index: int
    generation_id: str
    was_correct: bool
    issue_type: str | None  # "wrong_computation", "missing_context", etc.
    brief_description: str
```

### Phase 3: Root Cause Analysis

**Goal:** For each problematic generation, WHY did it go wrong?

**Input:** List of problematic generations from Phase 2

**Actions:**
- Examine full context of the turn
- Check against common failure modes:
  - Task misinterpretation (understood task incorrectly)
  - Missing context (didn't read required docs)
  - Wrong computation (math/logic error)
  - Format error (right answer, wrong format)
  - Domain misunderstanding (didn't understand domain terms)
  - Prompt ambiguity (prompt was unclear)
- Allow agent to express new failure mode if none fit

**Output:**
```python
@dataclass
class RootCauseAnalysis:
    primary_cause: str  # "category: specific_cause"
    supporting_evidence: list[str]
    key_decision_points: list[DecisionPoint]
    confidence: str  # "high", "medium", "low"
```

### Phase 4: Verification Loop

**Goal:** Validate the RCA makes sense

**Actions:**
- Review the RCA against the trace evidence
- Check for logical consistency
- Verify the causal chain is complete

**Decision:**
- If valid: Accept RCA and proceed to final report
- If invalid: Return to Phase 3 with feedback (max 3 iterations)

**Output:**
```python
@dataclass
class VerificationResult:
    is_valid: bool
    feedback: str | None  # If invalid, what to reconsider
    iteration: int
```

## Implementation Approach

### Option A: Multi-Strategy Agent

Use multiple `@strategy` decorated methods, called in sequence:

```python
class TraceAnalyzerAgent(Agent):
    @strategy(CodeActStrategy(max_iterations=5))
    async def phase1_overview(self) -> OverviewResult:
        """Get overview of the trace..."""

    @strategy(CodeActStrategy(max_iterations=15))
    async def phase2_generation_analysis(self) -> list[GenerationAnalysis]:
        """Analyze each generation call..."""

    @strategy(CodeActStrategy(max_iterations=15))
    async def phase3_root_cause(self, problematic_gens: list) -> RootCauseAnalysis:
        """Determine root cause..."""

    @strategy(CodeActStrategy(max_iterations=5))
    async def phase4_verify(self, rca: RootCauseAnalysis) -> VerificationResult:
        """Verify the RCA..."""

    async def _analyze(self, trace_path: str) -> dict:
        """Orchestrate the 4 phases."""
        overview = await self.phase1_overview()
        gen_analysis = await self.phase2_generation_analysis()

        problematic = [g for g in gen_analysis if not g.was_correct]

        for iteration in range(3):
            rca = await self.phase3_root_cause(problematic)
            verification = await self.phase4_verify(rca)
            if verification.is_valid:
                break

        return self._build_report(overview, gen_analysis, rca)
```

### Option B: Single Strategy with Structured Prompting

Keep single strategy but with explicit phase instructions in prompt:

```python
@strategy(CodeActStrategy(max_iterations=50))
async def diagnose(self) -> dict:
    """
    Execute in EXACTLY this order:

    ## PHASE 1: Overview
    Call get_overview() and get_eval_context()
    Note the outcome and any mechanical findings.

    ## PHASE 2: Generation Analysis
    For each LLM turn, evaluate if the response was correct.
    Mark which turns had issues.

    ## PHASE 3: Root Cause Analysis
    For problematic turns, determine WHY using these categories:
    - task_misinterpretation
    - missing_context
    - wrong_computation
    ...

    ## PHASE 4: Verification
    Review your RCA. Does it fully explain the failure?
    If not, revise. Max 3 revisions.
    """
```

### Recommendation: Option A (Multi-Strategy)

Benefits:
- Clear phase boundaries
- Easier to debug individual phases
- Each phase can have appropriate iteration limits
- Structured outputs between phases

## Common Failure Modes Prompt

For Phase 3, include a reference list:

```markdown
## Common Failure Modes

1. **task_misinterpretation** - Agent understood the task incorrectly
   - Misread "all applicable" as "best applicable"
   - Confused "rate" with "count"
   - Wrong aggregation level (per-item vs total)

2. **missing_context** - Failed to read required documentation
   - Didn't read manual/readme files
   - Skipped domain glossary
   - Missed prerequisite data files

3. **wrong_computation** - Correct understanding, incorrect execution
   - Math errors
   - Off-by-one bugs
   - Type coercion issues

4. **format_error** - Correct answer, wrong format
   - Missing units
   - Wrong precision
   - Incorrect delimiter

5. **domain_misunderstanding** - Misunderstood domain terminology
   - Industry-specific terms
   - Abbreviations
   - Contextual meanings

6. **prompt_ambiguity** - System prompt was unclear
   - Conflicting instructions
   - Missing specification
   - Implicit assumptions

7. **tool_misuse** - Incorrect use of available tools
   - Wrong API calls
   - Incorrect parameters
   - Misinterpreted results

8. **timeout/resource** - Hit resource limits
   - Max iterations
   - Token limits
   - Time limits
```

## Files to Modify

- `util/e2e_optimization/src/e2e_optimization/analyzer_agent.py` - Main implementation
- `util/e2e_optimization/src/e2e_optimization/diagnostic_report.py` - Add new dataclasses

## Verification Criteria

1. Correct diagnosis on 1681_hard (task_misinterpretation: "lowest fee" vs "all fees")
2. Correct diagnosis on 1753_hard (same issue)
3. Phase outputs are structured and traceable
4. Verification loop catches invalid RCAs
